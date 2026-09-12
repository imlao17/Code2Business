import Foundation
import Combine

/// 全局应用状态：项目选择、流程条轮询、任务进度、错误提示。
@MainActor
final class AppState: ObservableObject {
    let sidecar = SidecarController()
    let api = APIClient.shared

    enum Phase: Equatable {
        case starting(String)
        case ready
        case failed(String)
    }

    @Published var phase: Phase = .starting("正在启动本地分析引擎…")
    @Published var projects: [Project] = []
    @Published var currentProject: Project?
    @Published var pipeline: Pipeline?
    @Published var runningJob: LabelJob?
    @Published var autoBanner: String?
    @Published var lastError: String?

    private var pipelineTimer: Timer?
    private var jobTimer: Timer?
    private var pollToken = UUID()

    // MARK: - 启动

    func start() async {
        do {
            try await sidecar.launch()
            phase = .ready
            await refreshProjects()
            await refreshPipeline()
            startPipelinePolling()
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    func retryStart() async { await start() }

    // MARK: - 项目

    func refreshProjects() async {
        do {
            projects = try await api.get([Project].self, "projects")
            if let current = currentProject, !projects.contains(where: { $0.id == current.id }) {
                currentProject = nil
            }
            if currentProject == nil, let first = projects.first {
                select(first)
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    func select(_ project: Project) {
        currentProject = project
        runningJob = nil
        stopJobPolling()
        Task { await refreshPipeline() }
    }

    // MARK: - 流程条

    func refreshPipeline() async {
        guard let pid = currentProject?.id else {
            pipeline = nil
            return
        }
        do {
            pipeline = try await api.get(Pipeline.self, "projects/\(pid)/pipeline")
        } catch {
            pipeline = nil
        }
    }

    func startPipelinePolling() {
        pipelineTimer?.invalidate()
        let timer = Timer(timeInterval: 5, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.runningJob != nil || self.pipeline?.steps.contains(where: { $0.status == "running" }) == true else { return }
                await self.refreshPipeline()
                await self.refreshRunningJob()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        pipelineTimer = timer
    }

    // MARK: - 动作：扫描 / 一键分析 / 标注

    func startScan() async {
        guard let pid = currentProject?.id else { return }
        do {
            try await api.send("projects/\(pid)/scan", method: "POST")
            autoBanner = "扫描任务已启动"
            await trackRunningJob()
        } catch {
            lastError = error.localizedDescription
        }
    }

    func startAutoRun() async {
        guard let pid = currentProject?.id else { return }
        do {
            let info = try await api.request(AutoRunInfo.self, "projects/\(pid)/auto-run",
                                             method: "POST",
                                             body: try JSONSerialization.data(withJSONObject: ["run_ai": true]))
            if !info.aiReady {
                autoBanner = "尚未配置 AI 服务：本次将只完成扫描与图谱；在「设置 → AI 服务」配置后可继续自动归纳"
            } else {
                autoBanner = "一键分析已启动：扫描 → AI 归纳 → 模块分析。可离开窗口，进度在流程条显示"
            }
            await trackRunningJob()
        } catch {
            lastError = error.localizedDescription
        }
    }

    struct AutoRunInfo: Codable {
        let ok: Bool
        let jobId: Int
        let aiReady: Bool
        let message: String?
    }

    func startLabel() async {
        guard let pid = currentProject?.id else { return }
        do {
            let info = try await api.request(JobRef.self, "projects/\(pid)/label", method: "POST",
                                             body: try JSONSerialization.data(withJSONObject: ["limit": 100, "concurrency": 16]),
                                             timeout: APIClient.longTimeout)
            _ = info
            autoBanner = "标注任务已启动，后台执行中"
            await trackRunningJob()
        } catch {
            lastError = error.localizedDescription
        }
    }

    struct JobRef: Codable { let ok: Bool; let jobId: Int }

    /// 找到当前运行中的任务并开始轮询。
    func trackRunningJob() async {
        await refreshPipeline()
        guard let pid = currentProject?.id,
              let pipeline,
              let running = pipeline.steps.first(where: { $0.status == "running" }) else { return }
        // pipeline 只带 stage，不带 job id；用 scan-tasks 接口拿最新任务
        do {
            let tasks = try await api.get([ScanTaskInfo].self, "projects/\(pid)/scan-tasks")
            if let latest = tasks.first {
                runningJob = LabelJob(jobId: latest.id, status: latest.status,
                                      stage: latest.stage, progress: latest.progress,
                                      total: latest.total, result: nil, error: nil)
                startJobPolling(projectId: pid, jobId: latest.id)
            }
        } catch {
            // 轮询接口不可用时只展示流程条进度
            _ = running
        }
    }

    struct ScanTaskInfo: Codable {
        let id: Int
        let status: String
        let stage: String?
        let progress: Int?
        let total: Int?
    }

    func startJobPolling(projectId: Int, jobId: Int) {
        stopJobPolling()
        let token = pollToken
        let timer = Timer(timeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, token == self.pollToken, self.currentProject?.id == projectId else { return }
                await self.refreshRunningJob(jobId: jobId)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        jobTimer = timer
    }

    func stopJobPolling() {
        jobTimer?.invalidate()
        jobTimer = nil
        pollToken = UUID()
    }

    func refreshRunningJob(jobId: Int? = nil) async {
        guard let pid = currentProject?.id else { return }
        do {
            if let jobId {
                let job = try await api.get(LabelJob.self, "projects/\(pid)/label-jobs/\(jobId)")
                runningJob = job
                if job.status == "done" {
                    stopJobPolling()
                    if let result = job.result {
                        if result.aiRan == true {
                            autoBanner = "一键分析完成：规则 \(result.label?.rules ?? 0) 条 / 对象 \(result.label?.objects ?? 0) / 模块规格 \(result.modules?.analyzed ?? 0)"
                        } else {
                            autoBanner = "已完成确定性扫描与图谱"
                        }
                        if let note = result.notes?.first {
                            lastError = note
                        }
                    }
                    autoBanner = (autoBanner ?? "") + "。可到「文档与交付物」下载草稿版《客户交付总报告》；确认规则后可出正式版"
                    await refreshProjects()
                    await refreshPipeline()
                } else if job.status == "failed" {
                    stopJobPolling()
                    lastError = APIClient.friendly(job.error ?? "任务失败")
                    await refreshPipeline()
                }
            } else if let running = pipeline?.steps.first(where: { $0.status == "running" }) {
                runningJob = LabelJob(jobId: 0, status: "running", stage: running.stage,
                                      progress: running.progress, total: running.total,
                                      result: nil, error: nil)
            } else if runningJob?.status == "running" {
                runningJob = nil
            }
        } catch {
            // 轮询失败静默，下轮重试
        }
    }

    // MARK: - 删除项目

    func deleteCurrentProject() async {
        guard let pid = currentProject?.id else { return }
        do {
            try await api.send("projects/\(pid)", method: "DELETE", timeout: 120)
            currentProject = nil
            pipeline = nil
            stopJobPolling()
            await refreshProjects()
        } catch {
            lastError = error.localizedDescription
        }
    }
}

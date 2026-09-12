import SwiftUI

/// 新建项目：本地目录 / Git 地址 / zip 包。
struct ImportSheet: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss

    enum Source: String, CaseIterable, Identifiable {
        case local = "本地代码目录"
        case git = "Git 地址"
        case zip = "上传 Zip 包"
        var id: String { rawValue }
    }

    @State private var source: Source = .local
    @State private var name = ""
    @State private var localPath = ""
    @State private var gitUrl = ""
    @State private var zipURL: URL?
    @State private var importing = false
    @State private var progressText = ""
    @State private var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("新建项目（导入代码）").font(.title3.bold())

            Picker("代码来源", selection: $source) {
                ForEach(Source.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)

            Form {
                TextField("项目名称", text: $name, prompt: Text("如：订单管理系统"))

                switch source {
                case .local:
                    HStack {
                        TextField("代码目录", text: $localPath, prompt: Text("选择本机代码目录"))
                            .disabled(true)
                        Button("选择目录…") {
                            if let picked = PanelHelper.chooseDirectory() {
                                localPath = picked.path
                                if name.isEmpty { name = picked.name }
                            }
                        }
                    }
                    Text("仅关联此目录，不会复制或删除你的原始代码。")
                        .font(.caption).foregroundStyle(.secondary)
                case .git:
                    TextField("Git 地址", text: $gitUrl, prompt: Text("https://github.com/xxx/xxx.git"))
                case .zip:
                    HStack {
                        TextField("Zip 文件", text: .constant(zipURL?.lastPathComponent ?? ""))
                            .disabled(true)
                        Button("选择文件…") { zipURL = PanelHelper.chooseZip() }
                        if zipURL != nil {
                            Button(role: .destructive) { zipURL = nil } label: { Image(systemName: "xmark.circle.fill") }
                                .buttonStyle(.plain)
                        }
                    }
                }
            }

            if importing {
                HStack { ProgressView().controlSize(.small); Text(progressText).foregroundStyle(.secondary) }
            }
            if let errorText {
                Text(errorText).font(.caption).foregroundStyle(.red)
            }

            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(importing ? "导入中…" : "导入代码") { Task { await doImport() } }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(importing || !canSubmit)
            }
        }
        .padding(22)
        .frame(width: 520)
    }

    private var canSubmit: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && (
            (source == .local && !localPath.isEmpty) ||
            (source == .git && !gitUrl.trimmingCharacters(in: .whitespaces).isEmpty) ||
            (source == .zip && zipURL != nil)
        )
    }

    private func doImport() async {
        importing = true
        errorText = nil
        defer { importing = false }
        do {
            let trimmedName = name.trimmingCharacters(in: .whitespaces)
            var response: ImportResponse?
            switch source {
            case .local:
                progressText = "正在关联本地代码目录…"
                response = try await state.api.request(ImportResponse.self, "projects/import-local",
                                                       method: "POST",
                                                       body: try JSONSerialization.data(withJSONObject: [
                                                        "name": trimmedName, "source_path": localPath]))
            case .git:
                progressText = "正在后台克隆 Git 仓库…"
                response = try await postForm(fields: ["name": trimmedName, "git_url": gitUrl])
            case .zip:
                progressText = "上传并解压 zip 代码包…"
                guard let zipURL else { return }
                let data = try Data(contentsOf: zipURL)
                try await state.api.upload("projects/import",
                                           fields: ["name": trimmedName],
                                           fileField: "file", fileName: zipURL.lastPathComponent,
                                           fileData: data)
                try await finishImport(projectId: -1, jobId: nil, needLookup: true)
                return
            }
            guard let response else { return }
            try await finishImport(projectId: response.projectId, jobId: response.jobId,
                                   needLookup: false,
                                   cloneRunning: response.importStatus == "running")
        } catch {
            errorText = error.localizedDescription
        }
    }

    /// zip 导入的响应不含语义化结果，导入完成后直接按项目名查找新项目。
    private func finishImport(projectId: Int, jobId: Int?, needLookup: Bool,
                              cloneRunning: Bool = false) async throws {
        if cloneRunning, let jobId {
            try await pollImportJob(projectId: projectId, jobId: jobId)
        }
        await state.refreshProjects()
        var target: Project?
        if needLookup, projectId <= 0 {
            target = state.projects.first { $0.name == name.trimmingCharacters(in: .whitespaces) }
        } else {
            target = state.projects.first { $0.id == projectId }
        }
        if let target { state.select(target) }
        state.autoBanner = "代码导入完成，点击「一键分析」开始"
        dismiss()
    }

    /// /projects/import 走 Form 表单（zip 或 git_url 二选一），git 路径用这里。
    private func postForm(fields: [String: String]) async throws -> ImportResponse {
        var components = URLComponents()
        components.queryItems = fields.map { URLQueryItem(name: $0.key, value: $0.value) }
        var request = URLRequest(url: state.api.baseURL.appendingPathComponent("projects/import"))
        request.httpMethod = "POST"
        request.timeoutInterval = 600
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = components.percentEncodedQuery?.data(using: .utf8)
        let (data, response) = try await URLSession.shared.data(for: request)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        guard let http = response as? HTTPURLResponse else { throw APIClient.APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIClient.APIError.http(http.statusCode, APIClient.detail(from: data))
        }
        return try decoder.decode(ImportResponse.self, from: data)
    }

    private func pollImportJob(projectId: Int, jobId: Int) async throws {
        for _ in 0..<1200 {  // 最多 60 分钟（超大仓库克隆）
            let job = try await state.api.get(LabelJob.self, "projects/\(projectId)/label-jobs/\(jobId)")
            if job.status == "done" { return }
            if job.status == "failed" {
                throw APIClient.APIError.http(500, APIClient.friendly(job.error ?? "导入失败"))
            }
            if let progress = job.progress, let total = job.total, total > 0 {
                progressText = "克隆中：\(progress)/\(total)"
            }
            try await Task.sleep(nanoseconds: 3_000_000_000)
        }
        throw APIClient.APIError.http(500, "Git 克隆超时")
    }
}

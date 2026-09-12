import Foundation

/// 本地 Python 分析引擎（sidecar）的生命周期管理。
///
/// 与旧 Tauri 壳保持完全一致的职责面：
/// - 数据目录 `~/Library/Application Support/com.lao17.code2business`（沿用 bundle id，老用户数据不迁移）；
/// - 注入同一组 `C2B_*` 环境变量；
/// - 8123 端口 20 秒就绪轮询；端口已被监听则复用（软单实例）；
/// - 退出时终止 sidecar；`C2B_DESKTOP_PARENT_PID` 让 PyInstaller onefile
///   的真实服务进程在壳被强杀时自行退出（sidecar 内置 watcher）。
final class SidecarController {
    static let port = 8123
    static let baseURL = URL(string: "http://127.0.0.1:\(port)")!

    private(set) var process: Process?
    private let bundleID = "com.lao17.code2business"

    enum EngineError: LocalizedError {
        case resourceMissing(String)
        case devPythonMissing(String)
        case startupTimeout

        var errorDescription: String? {
            switch self {
            case .resourceMissing(let path):
                return "应用包内缺少分析引擎：\(path)"
            case .devPythonMissing(let hint):
                return "开发模式未找到 Python：\(hint)（可用 C2B_DESKTOP_PYTHON 指定）"
            case .startupTimeout:
                return "本地分析引擎启动超时（20 秒）。请重启 C2B Desktop；若反复出现请重启 Mac。"
            }
        }
    }

    // MARK: - 目录

    var dataRoot: URL {
        if let override = ProcessInfo.processInfo.environment["C2B_DESKTOP_DATA_DIR"], !override.isEmpty {
            return URL(fileURLWithPath: (override as NSString).expandingTildeInPath)
        }
        return URL(fileURLWithPath: "Library/Application Support/\(bundleID)", isDirectory: true, relativeTo: FileManager.default.homeDirectoryForCurrentUser)
    }

    var sourcesDir: URL { dataRoot.appendingPathComponent("sources", isDirectory: true) }
    var settingsDir: URL { dataRoot.appendingPathComponent("settings", isDirectory: true) }
    var dbURL: URL { dataRoot.appendingPathComponent("c2b.db") }

    func prepareDirectories() throws {
        for dir in [dataRoot, sourcesDir, settingsDir] {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }

    // MARK: - 就绪探测

    /// 引擎是否已在服务（软单实例：已运行则直接复用）。
    func backendReady(timeout: TimeInterval = 0.3) -> Bool {
        let semaphore = DispatchSemaphore(value: 0)
        var up = false
        var request = URLRequest(url: SidecarController.baseURL.appendingPathComponent("healthz"))
        request.timeoutInterval = 2
        let task = URLSession.shared.dataTask(with: request) { data, response, _ in
            if let http = response as? HTTPURLResponse, http.statusCode == 200, data != nil { up = true }
            semaphore.signal()
        }
        task.resume()
        _ = semaphore.wait(timeout: .now() + timeout)
        return up
    }

    /// 轮询等待引擎就绪，超时抛错。
    func waitUntilReady(deadline: TimeInterval = 20) async throws {
        let start = Date()
        while Date().timeIntervalSince(start) < deadline {
            if await isHealthy() { return }
            try await Task.sleep(nanoseconds: 200_000_000)
        }
        throw EngineError.startupTimeout
    }

    func isHealthy() async -> Bool {
        var request = URLRequest(url: SidecarController.baseURL.appendingPathComponent("healthz"))
        request.timeoutInterval = 2
        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    // MARK: - 启动

    /// 启动引擎；若已在运行（软单实例）直接返回。
    func launch() async throws {
        try prepareDirectories()
        if backendReady() { return }

        let p = Process()
        var environment: [String: String] = ProcessInfo.processInfo.environment
        environment["C2B_DESKTOP_MODE"] = "1"
        environment["C2B_DB_URL"] = "sqlite:///\(dbURL.path)"
        environment["C2B_DATA_ROOT"] = sourcesDir.path
        environment["C2B_CONFIG_DIR"] = settingsDir.path
        environment["C2B_DESKTOP_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        environment["C2B_ALLOW_PRIVATE_LLM"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"

        if let repoRoot = devRepoRoot() {
            // 开发模式：仓库 .venv + uvicorn，便于不改引擎直接调试壳
            let pythonPath = ProcessInfo.processInfo.environment["C2B_DESKTOP_PYTHON"]
                ?? repoRoot.appendingPathComponent(".venv/bin/python").path
            guard FileManager.default.isExecutableFile(atPath: pythonPath) else {
                throw EngineError.devPythonMissing(pythonPath)
            }
            p.executableURL = URL(fileURLWithPath: pythonPath)
            p.arguments = ["-m", "uvicorn", "c2b.api.main:app",
                           "--host", "127.0.0.1", "--port", String(Self.port)]
            environment["PYTHONPATH"] = repoRoot.appendingPathComponent("core/src").path
        } else {
            // 正式模式：PyInstaller onefile 产物在 Contents/Resources/c2b-api
            let sidecar = SidecarController.resourceURL.appendingPathComponent("c2b-api")
            guard FileManager.default.isExecutableFile(atPath: sidecar.path) else {
                throw EngineError.resourceMissing(sidecar.path)
            }
            p.executableURL = sidecar
        }

        p.environment = environment
        p.standardInput = FileHandle.nullDevice
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        process = p
        try await waitUntilReady()
    }

    /// 仅在以 `swift run` 直跑（.build 产物）时启用开发模式。
    func devRepoRoot() -> URL? {
        if let root = ProcessInfo.processInfo.environment["C2B_DEV_REPO_ROOT"], !root.isEmpty {
            return URL(fileURLWithPath: (root as NSString).expandingTildeInPath)
        }
        guard let executableURL = Bundle.main.executableURL,
              executableURL.pathComponents.contains(".build") else { return nil }
        // .build/debug/C2BDesktop → 向上找包含 core/pyproject.toml 的目录
        var dir = executableURL.deletingLastPathComponent()
        for _ in 0..<6 {
            if FileManager.default.fileExists(atPath: dir.appendingPathComponent("core/pyproject.toml").path) {
                return dir
            }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    static var resourceURL: URL {
        Bundle.main.resourceURL ?? Bundle.main.bundleURL.appendingPathComponent("Contents/Resources")
    }

    // MARK: - 终止

    func terminate() {
        guard let p = process, p.isRunning else { return }
        p.terminate()
        // 给优雅退出 2 秒，超时强杀
        let deadline = Date().addingTimeInterval(2)
        while p.isRunning && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if p.isRunning { p.terminate() }
    }
}

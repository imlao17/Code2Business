import Foundation

/// C2B 本地引擎的 HTTP 客户端（全部走 127.0.0.1 loopback）。
struct APIClient {
    static let shared = APIClient()
    static let longTimeout: TimeInterval = 330  // LLM 单次分析后端超时 5 分钟

    var baseURL: URL { SidecarController.baseURL.appendingPathComponent("api") }

    enum APIError: LocalizedError {
        case http(Int, String)
        case invalidResponse

        var errorDescription: String? {
            switch self {
            case .http(let code, let detail): return detail
            case .invalidResponse: return "本地引擎返回了无法解析的响应"
            }
        }
    }

    // MARK: - 基础请求

    func request<T: Decodable>(_ type: T.Type, _ path: String,
                               method: String = "GET",
                               body: Data? = nil,
                               contentType: String = "application/json",
                               timeout: TimeInterval = 60) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let body {
            request.httpBody = body
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        return try decode(type, data, response)
    }

    func get<T: Decodable>(_ type: T.Type, _ path: String,
                           query: [String: String] = [:],
                           timeout: TimeInterval = 60) async throws -> T {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        var request = URLRequest(url: components.url!)
        request.timeoutInterval = timeout
        let (data, response) = try await URLSession.shared.data(for: request)
        return try decode(type, data, response)
    }

    func send(_ path: String, method: String, body: [String: Any]? = nil,
              timeout: TimeInterval = 60) async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = timeout
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        _ = try decode(EmptyResponse.self, data, response)
    }

    /// 下载二进制（Word/Excel/zip），返回原始数据。
    func download(_ path: String, query: [String: String] = [:]) async throws -> Data {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        let (data, response) = try await URLSession.shared.data(for: URLRequest(url: components.url!))
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(http.statusCode, Self.detail(from: data))
        }
        return data
    }

    /// multipart/form-data 上传（zip 导入）。
    func upload(_ path: String, fields: [String: String],
                fileField: String, fileName: String, fileData: Data) async throws {
        let boundary = "C2B-\(UUID().uuidString)"
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.timeoutInterval = 600  // zip 解压在请求中完成，大包需要长超时
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var body = Data()
        for (name, value) in fields {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append("Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!)
            body.append("\(value)\r\n".data(using: .utf8)!)
        }
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(fileName)\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: application/zip\r\n\r\n".data(using: .utf8)!)
        body.append(fileData)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        request.httpBody = body
        let (data, response) = try await URLSession.shared.data(for: request)
        _ = try decode(ImportResponse.self, data, response)
    }

    // MARK: - 解码

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data,
                                      _ response: URLResponse) throws -> T {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(http.statusCode, Self.detail(from: data))
        }
        if type == EmptyResponse.self { return EmptyResponse() as! T }
        do {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            return try decoder.decode(type, from: data)
        } catch {
            throw APIError.invalidResponse
        }
    }

    static func detail(from data: Data) -> String {
        if let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let detail = object["detail"] as? String {
            return friendly(detail)
        }
        let text = String(data: data, encoding: .utf8) ?? ""
        return text.isEmpty ? "请求失败" : String(text.prefix(200))
    }

    /// 与 Web 端 errors.js 同口径的白话错误映射。
    static func friendly(_ detail: String) -> String {
        let lowered = detail.lowercased()
        if lowered.contains("缺少 api key") || lowered.contains("llm 设置") || lowered.contains("api_key") {
            return "还没有配置 AI 服务：打开「设置 → AI 服务」填入 API Key 后重试。不配置也可以先完成扫描和查看图谱。"
        }
        if lowered.contains("401") || lowered.contains("unauthorized") || lowered.contains("invalid") && lowered.contains("key") || lowered.contains("余额") {
            return "AI 服务拒绝了当前请求：请检查 API Key 是否正确、账户是否有余额。"
        }
        if lowered.contains("connect") || lowered.contains("timeout") || lowered.contains("network") {
            return "连不上 AI 服务：请检查网络与「设置 → AI 服务」中的服务地址。"
        }
        if lowered.contains("database is locked") {
            return "本地数据库正忙（通常是上一个大任务还在写入）。请稍等片刻再试。"
        }
        if lowered.contains("已有运行中的任务") {
            return "当前项目已有任务在运行，请等它完成后再操作。"
        }
        return detail
    }
}

struct EmptyResponse: Codable {}

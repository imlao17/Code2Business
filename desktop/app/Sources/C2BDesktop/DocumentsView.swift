import SwiftUI

struct DocumentsView: View {
    @EnvironmentObject private var state: AppState
    @State private var docTypes: [DocType] = []
    @State private var draft = false
    @State private var generating: String?
    @State private var previewMarkdown: String?
    @State private var previewTitle: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                heroCard
                modeSwitch
                docGrid
                if let previewMarkdown {
                    previewCard(previewMarkdown)
                }
            }
            .padding(20)
        }
        .task { await load() }
    }

    private func load() async {
        docTypes = (try? await state.api.get([DocType].self, "documents/types")) ?? []
    }

    private var combined: DocType? { docTypes.first { $0.key == "combined" } }
    private var others: [DocType] { docTypes.filter { $0.key != "combined" } }

    private var heroCard: some View {
        HStack {
            VStack(alignment: .leading, spacing: 6) {
                Text("客户交付总报告").font(.title2.bold()).foregroundStyle(.tint)
                Text("一份文档讲清整个系统：概览、功能、流程、核心规则、验收与待确认。面向不需要读代码的客户与业务人员。")
                    .font(.callout).foregroundStyle(.secondary)
                    .frame(maxWidth: 560, alignment: .leading)
            }
            Spacer()
            VStack(spacing: 8) {
                Button { Task { await download("combined", "word") } } label: {
                    Label("下载 Word 总报告", systemImage: "arrow.down.doc.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(generating != nil)
                Button("在网页中预览".replacingOccurrences(of: "网页", with: "窗口")) {
                    Task { await preview("combined") }
                }
                .disabled(generating != nil)
            }
        }
        .padding(18)
        .background(.tint.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }

    private var modeSwitch: some View {
        HStack {
            Toggle(isOn: $draft) {
                Text(draft ? "草稿（含 AI 未确认项）" : "正式（仅人工确认项）")
            }
            .toggleStyle(.switch)
            Text(draft ? "适合内部评审" : "建议先完成规则确认再生成正式版")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            if let generating {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text("正在生成 \(generating)…").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
        .onChange(of: draft) { _ in
            previewMarkdown = nil
            previewTitle = nil
        }
    }

    private var docGrid: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 12)], spacing: 12) {
            ForEach(others) { type in
                VStack(alignment: .leading, spacing: 8) {
                    Text(type.title).font(.callout.bold())
                    Text(type.desc).font(.caption).foregroundStyle(.secondary)
                        .lineLimit(3).frame(height: 44, alignment: .top)
                    HStack {
                        Button("预览") { Task { await preview(type.key) } }
                            .controlSize(.small)
                        Button("Markdown") { Task { await download(type.key, "md") } }
                            .controlSize(.small)
                        Button("Word") { Task { await download(type.key, "word") } }
                            .controlSize(.small)
                    }
                }
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
            }
        }
    }

    private func previewCard(_ markdown: String) -> some View {
        CardView(previewTitle ?? "预览") {
            VStack(alignment: .leading, spacing: 8) {
                if let attributed = try? AttributedString(
                    markdown: trimHeader(markdown),
                    options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)) {
                    Text(attributed).font(.callout)
                } else {
                    Text(markdown).font(.system(.caption, design: .monospaced))
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    /// 去掉引用行头（生成说明），预览从正文开始
    private func trimHeader(_ markdown: String) -> String {
        guard let firstBody = markdown.range(of: "\n\n") else { return markdown }
        return String(markdown[firstBody.upperBound...])
    }

    // MARK: - 动作

    private func preview(_ key: String) async {
        guard let pid = state.currentProject?.id else { return }
        generating = key
        defer { generating = nil }
        do {
            struct DocResponse: Codable { let markdown: String; let title: String }
            let response = try await state.api.get(DocResponse.self,
                                                   "projects/\(pid)/documents/\(key)",
                                                   query: ["fmt": "md", "draft": draft ? "true" : "false"],
                                                   timeout: APIClient.longTimeout)
            previewTitle = response.title + (draft ? "（草稿）" : "")
            previewMarkdown = response.markdown
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private func download(_ key: String, _ fmt: String) async {
        guard let pid = state.currentProject?.id else { return }
        generating = key
        defer { generating = nil }
        do {
            let title = combined?.key == key ? "客户交付总报告"
                : (docTypes.first { $0.key == key }?.title ?? key)
            if fmt == "word" {
                let data = try await state.api.download("projects/\(pid)/documents/\(key)",
                                                        query: ["fmt": "word", "draft": draft ? "true" : "false"])
                guard let url = PanelHelper.savePanel(name: title, allowedExtensions: ["docx"]) else { return }
                try data.write(to: url)
                state.autoBanner = "已下载：\(url.lastPathComponent)"
            } else {
                struct DocResponse: Codable { let markdown: String; let title: String }
                let response = try await state.api.get(DocResponse.self,
                                                       "projects/\(pid)/documents/\(key)",
                                                       query: ["fmt": "md", "draft": draft ? "true" : "false"],
                                                       timeout: APIClient.longTimeout)
                guard let url = PanelHelper.savePanel(name: response.title, allowedExtensions: ["md"]) else { return }
                try response.markdown.write(to: url, atomically: true, encoding: .utf8)
                state.autoBanner = "已下载：\(url.lastPathComponent)"
            }
        } catch {
            state.lastError = error.localizedDescription
        }
    }
}

// MARK: - 帮助

struct HelpView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                CardView("第一次使用？三步出结果") {
                    VStack(alignment: .leading, spacing: 8) {
                        helpStep(1, "新建项目", "侧栏底部「#项目」菜单 → 新建，或工具栏 +：选择本地目录、Git 地址或 zip 包。")
                        helpStep(2, "一键分析", "工具栏「⚡ 一键分析」自动完成扫描、AI 业务归纳和模块分析；期间可离开，流程条显示进度。")
                        helpStep(3, "拿到报告", "「文档与交付物」下载《客户交付总报告》（Word）；在「流程与规则」确认规则后可出正式版。")
                    }
                }
                CardView("名词解释") {
                    VStack(alignment: .leading, spacing: 8) {
                        glossary("模块", "把代码按目录划分出的“功能区”，相当于系统的零件划分。")
                        glossary("调用边", "“A 函数调用了 B 函数”这样的一条关系，越多说明功能联系越紧密。")
                        glossary("业务域 / 业务对象", "业务域是业务的大类（如订单、库存）；业务对象是大类里具体管理的东西。")
                        glossary("规则卡片", "一条业务规则的档案：什么条件下、系统做什么、数据怎么变、依据在代码哪个位置。")
                        glossary("AI 可信度", "AI 对这条结论有多大把握（0-100%），只是参考分，最终以人工确认为准。")
                        glossary("V1-V4（静态证据）", "结论在代码里的证据强度：V4 多处代码互相印证，V1 只有单一线索。")
                        glossary("R0-R3（运行验证）", "有没有被实际运行证实：R0 没有，R1 测试过，R2 预发布，R3 生产验证。")
                        glossary("草稿 / 正式", "草稿包含 AI 未确认内容（内部评审用）；正式版只含人工确认过的知识（交付客户用）。")
                    }
                }
                CardView("常见问题") {
                    VStack(alignment: .leading, spacing: 8) {
                        glossary("一键分析失败？", "看错误提示：没配 AI Key 只做扫描；Key 无效或欠费会在提示中说明。")
                        glossary("文档内容很少？", "正式口径只含人工确认的知识。先在「流程与规则」确认（可用按可信度一键确认），或先出草稿版。")
                        glossary("数据去哪了？", "代码与图谱都保存在本机 ~/Library/Application Support/com.lao17.code2business。只有 AI 标注会把少量代码证据发给你自己配置的大模型服务。")
                    }
                }
            }
            .padding(20)
        }
    }

    private func helpStep(_ n: Int, _ title: String, _ desc: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            ZStack {
                Circle().fill(Color.accentColor.opacity(0.15))
                Text("\(n)").font(.caption.bold()).foregroundStyle(.tint)
            }
            .frame(width: 22, height: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout.bold())
                Text(desc).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func glossary(_ term: String, _ desc: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(term).font(.callout.bold()).frame(width: 150, alignment: .leading)
            Text(desc).font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
    }
}

// MARK: - 设置（LLM）

struct SettingsView: View {
    @EnvironmentObject private var state: AppState
    @State private var provider = "openai_compatible"
    @State private var mode = "api"
    @State private var baseURL = "https://api.deepseek.com/v1"
    @State private var model = "deepseek-chat"
    @State private var apiKey = ""
    @State private var hasKey = false
    @State private var keyMasked = ""
    @State private var testing = false
    @State private var testResult: String?

    var body: some View {
        Form {
            Section("AI 服务（用于自动归纳业务规则与模块规格）") {
                Picker("模式", selection: $mode) {
                    Text("API（云端）").tag("api")
                    Text("Mock（模拟演示）").tag("mock")
                }
                .pickerStyle(.radioGroup)
                if mode == "api" {
                    Picker("Provider", selection: $provider) {
                        Text("OpenAI Compatible（OpenAI / DeepSeek / 自建）").tag("openai_compatible")
                        Text("Anthropic（Claude 原生）").tag("anthropic")
                    }
                    TextField("API 地址", text: $baseURL)
                    TextField("模型", text: $model)
                    SecureField(hasKey ? "已保存：\(keyMasked)（留空则不修改）" : "API Key（sk-...）", text: $apiKey)
                    if let testResult {
                        Text(testResult).font(.caption).foregroundStyle(testResult.hasPrefix("✓") ? .green : .red)
                    }
                    HStack {
                        Button("测试连接") { Task { await test() } }.disabled(testing)
                        if testing { ProgressView().controlSize(.small) }
                        Spacer()
                        Button("保存") { Task { await save() } }
                            .buttonStyle(.borderedProminent)
                    }
                } else {
                    Text("Mock 用内置模拟数据演示流程，不做真实业务分析。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text("没有 Key 也可以先扫描、看图谱；推荐 DeepSeek（便宜、国内直连）。密钥保存在本机。")
                    .font(.caption).foregroundStyle(.tertiary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520, height: 440)
        .task { await load() }
    }

    private func load() async {
        guard let settings = try? await state.api.get(LlmSettings.self, "settings/llm") else { return }
        provider = settings.provider ?? provider
        mode = settings.mode ?? mode
        baseURL = settings.baseURL ?? baseURL
        model = settings.model ?? model
        hasKey = settings.hasKey
        keyMasked = settings.keyMasked ?? ""
    }

    private func save() async {
        var body: [String: Any] = ["provider": provider, "mode": mode,
                                   "base_url": baseURL, "model": model]
        if !apiKey.isEmpty { body["api_key"] = apiKey }
        do {
            try await state.api.send("settings/llm", method: "POST", body: body)
            apiKey = ""
            await load()
            testResult = "✓ 已保存"
        } catch {
            testResult = error.localizedDescription
        }
    }

    private func test() async {
        testing = true
        defer { testing = false }
        var body: [String: Any] = ["provider": provider, "mode": mode,
                                   "base_url": baseURL, "model": model]
        if !apiKey.isEmpty { body["api_key"] = apiKey }
        do {
            let result = try await state.api.request(LlmTestResult.self, "settings/llm/test",
                                                     method: "POST",
                                                     body: try JSONSerialization.data(withJSONObject: body),
                                                     timeout: APIClient.longTimeout)
            testResult = result.ok ? "✓ \(result.message ?? "连接成功")" : (result.message ?? "连接失败")
        } catch {
            testResult = error.localizedDescription
        }
    }
}

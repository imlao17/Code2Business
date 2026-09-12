import SwiftUI

struct RulesView: View {
    @EnvironmentObject private var state: AppState

    @State private var rules: [Rule] = []
    @State private var domains: [Domain] = []
    @State private var loading = false
    @State private var filterType: String?
    @State private var filterState: String?
    @State private var selectedRule: RuleDetail?
    @State private var selection = Set<Int>()
    @State private var note = ""
    @State private var highConfOpen = false
    @State private var highConfMin = 80.0
    @State private var highConfCount = 0
    @State private var busy = false

    let ruleTypes = ["validation": "校验", "calculation": "计算", "status": "状态",
                     "permission": "权限", "trigger": "自动触发", "exception": "异常", "other": "其他"]
    let stateLabels = ["ai_labeled": ("AI 识别", Color.gray), "confirmed": ("已确认", .green),
                       "modified": ("已修改", .orange), "discarded": ("已废弃", .red),
                       "conflict": ("存在冲突", .red)]

    var confirmedCount: Int { rules.filter { $0.state == "confirmed" || $0.state == "modified" }.count }

    var body: some View {
        VStack(spacing: 12) {
            guideBar
            filterBar
            if loading && rules.isEmpty {
                ProgressView().frame(maxHeight: .infinity)
            } else if rules.isEmpty {
                emptyView
            } else {
                ruleTable
            }
        }
        .padding(16)
        .task { await load() }
        .sheet(item: $selectedRule) { detail in
            RuleDetailSheet(detail: detail, note: $note) { action, fields in
                await confirm(detail.id, action: action, fields: fields)
            }
        }
        .sheet(isPresented: $highConfOpen) { highConfSheet }
    }

    // MARK: - 顶栏

    private var guideBar: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("AI 从代码中识别出的业务规则卡片").font(.callout.bold())
                Text("点开卡片核对代码证据后确认；只有确认/修改过的规则才进入正式交付文档。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text("已确认 \(confirmedCount) / 共 \(rules.count) 条").foregroundStyle(.secondary)
            Button("按可信度一键确认") { Task { await openHighConf() } }
            Button("导出已确认 Excel") { Task { await exportExcel() } }
                .disabled(confirmedCount == 0)
        }
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }

    private var filterBar: some View {
        HStack {
            Picker("类型", selection: $filterType) {
                Text("全部类型").tag(String?.none)
                ForEach(ruleTypes.sorted(by: { $0.key < $1.key }), id: \.key) { key, label in
                    Text(label).tag(String?.some(key))
                }
            }
            .frame(width: 140)
            Picker("状态", selection: $filterState) {
                Text("全部状态").tag(String?.none)
                ForEach(stateLabels.sorted(by: { $0.key < $1.key }), id: \.key) { key, pair in
                    Text(pair.0).tag(String?.some(key))
                }
            }
            .frame(width: 140)
            Button("查询") { Task { await load() } }
            Spacer()
            if !selection.isEmpty {
                Button("确认选中（\(selection.count)）") { Task { await batchConfirm() } }
                    .buttonStyle(.borderedProminent)
            }
            if busy { ProgressView().controlSize(.small) }
        }
    }

    private var emptyView: some View {
        VStack(spacing: 10) {
            Image(systemName: "checklist").font(.system(size: 34)).foregroundStyle(.tertiary)
            Text("还没有规则卡片").foregroundStyle(.secondary)
            Text("点击「一键分析」让 AI 归纳业务规则").font(.caption).foregroundStyle(.tertiary)
        }
        .frame(maxHeight: .infinity)
    }

    // MARK: - 表格

    private var ruleTable: some View {
        Table(rules, selection: $selection) {
            TableColumn("规则名称") { rule in
                Text(rule.displayName).lineLimit(1)
            }
            TableColumn("类型") { rule in
                Text(ruleTypes[rule.ruleType] ?? rule.ruleType)
            }
            TableColumn("模块") { rule in
                Text(rule.module ?? "-").lineLimit(1)
            }
            TableColumn("可信度") { rule in
                HStack(spacing: 6) {
                    ProgressView(value: Double(rule.confidence), total: 100)
                        .tint(rule.confidence >= 75 ? .green : rule.confidence >= 50 ? .orange : .red)
                        .frame(width: 60)
                    Text("\(rule.confidence)%").font(.caption.monospacedDigit())
                }
            }
            TableColumn("运行验证") { rule in
                StateTag(text: rule.runtimeLevel ?? "R0",
                         color: rule.runtimeLevel == "R3" ? .green : rule.runtimeLevel == "R0" ? .gray : .orange)
            }
            TableColumn("状态") { rule in
                let pair = stateLabels[rule.state] ?? (rule.state, Color.gray)
                StateTag(text: pair.0, color: pair.1)
            }
            TableColumn("证据") { rule in
                Text(rule.evidenceFile ?? "-").lineLimit(1).foregroundStyle(.secondary)
            }
        }
        .onChange(of: selection) { newValue in
            if newValue.count == 1, let id = newValue.first,
               id != selectedRule?.id, let row = rules.first(where: { $0.id == id }) {
                Task { await openDetail(row) }
            }
        }
    }

    private func openDetail(_ row: Rule) async {
        guard let pid = state.currentProject?.id else { return }
        do {
            selectedRule = try await state.api.get(RuleDetail.self, "projects/\(pid)/rules/\(row.id)")
            note = ""
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    // MARK: - 动作

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        loading = true
        defer { loading = false }
        do {
            var query: [String: String] = [:]
            if let filterType { query["rule_type"] = filterType }
            if let filterState { query["state"] = filterState }
            rules = try await state.api.get([Rule].self, "projects/\(pid)/rules", query: query)
            domains = (try? await state.api.get([Domain].self, "projects/\(pid)/domains")) ?? []
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private func confirm(_ ruleId: Int, action: String, fields: [String: String]? = nil) async {
        guard let pid = state.currentProject?.id else { return }
        busy = true
        defer { busy = false }
        do {
            var body: [String: Any] = ["action": action, "user": "desktop-user"]
            if !note.isEmpty { body["note"] = note }
            if let fields, action == "modify" { body["fields"] = fields }
            try await state.api.send("projects/\(pid)/rules/\(ruleId)/confirm", method: "POST", body: body)
            selectedRule = nil
            note = ""
            await load()
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private func batchConfirm() async {
        guard let pid = state.currentProject?.id, !selection.isEmpty else { return }
        busy = true
        defer { busy = false }
        do {
            try await state.api.send("projects/\(pid)/rules/confirm-batch", method: "POST",
                                     body: ["ids": Array(selection), "action": "confirm", "user": "desktop-user"])
            selection.removeAll()
            await load()
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private func openHighConf() async {
        guard let pid = state.currentProject?.id else { return }
        do {
            let preview = try await state.api.request(HighConfidencePreview.self,
                                                      "projects/\(pid)/rules/confirm-high-confidence",
                                                      method: "POST",
                                                      body: try JSONSerialization.data(withJSONObject: [
                                                        "min_confidence": Int(highConfMin), "dry_run": true]))
            highConfCount = preview.count
            highConfOpen = true
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private var highConfSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("按可信度一键确认").font(.title3.bold())
            Text("把 AI 可信度不低于阈值的「AI 识别」规则一次性确认为已核实。每一条都会写入确认记录，可追溯。")
                .font(.callout).foregroundStyle(.secondary)
            HStack {
                Text("可信度 ≥")
                Slider(value: $highConfMin, in: 50...100, step: 5) { _ in
                    Task { await refreshHighConfCount() }
                }
                .frame(width: 200)
                Text("\(Int(highConfMin))").font(.title3.bold().monospacedDigit())
            }
            Text("将确认 \(highConfCount) 条（不影响已确认/已废弃/冲突的规则）")
                .font(.callout)
            HStack {
                Spacer()
                Button("取消") { highConfOpen = false }.keyboardShortcut(.cancelAction)
                Button("确认这 \(highConfCount) 条") { Task { await runHighConf() } }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(highConfCount == 0)
            }
        }
        .padding(22)
        .frame(width: 460)
    }

    private func refreshHighConfCount() async {
        guard let pid = state.currentProject?.id else { return }
        let preview = try? await state.api.request(HighConfidencePreview.self,
                                                   "projects/\(pid)/rules/confirm-high-confidence",
                                                   method: "POST",
                                                   body: try JSONSerialization.data(withJSONObject: [
                                                    "min_confidence": Int(highConfMin), "dry_run": true]))
        highConfCount = preview?.count ?? 0
    }

    private func runHighConf() async {
        guard let pid = state.currentProject?.id else { return }
        busy = true
        defer { busy = false }
        do {
            struct Result: Codable { let confirmed: Int; let minConfidence: Int }
            let result = try await state.api.request(Result.self,
                                                     "projects/\(pid)/rules/confirm-high-confidence",
                                                     method: "POST",
                                                     body: try JSONSerialization.data(withJSONObject: [
                                                      "min_confidence": Int(highConfMin), "dry_run": false,
                                                      "user": "desktop-user"]))
            highConfOpen = false
            state.autoBanner = "已按可信度 ≥\(result.minConfidence) 确认 \(result.confirmed) 条规则"
            await load()
        } catch {
            state.lastError = error.localizedDescription
        }
    }

    private func exportExcel() async {
        guard let pid = state.currentProject?.id else { return }
        do {
            let data = try await state.api.download("projects/\(pid)/rules/export")
            guard let url = PanelHelper.savePanel(name: "业务规则清单", allowedExtensions: ["xlsx"]) else { return }
            try data.write(to: url)
            state.autoBanner = "已导出：\(url.lastPathComponent)"
        } catch {
            state.lastError = error.localizedDescription
        }
    }
}

// MARK: - 规则详情（含编辑模式）

struct RuleDetailSheet: View {
    @Environment(\.dismiss) private var dismiss
    let detail: RuleDetail
    @Binding var note: String
    let onAction: (String, [String: String]?) async -> Void

    @State private var editing = false
    @State private var form: [String: String] = [:]

    private let editableFields: [(String, String)] = [
        ("name", "规则名称"), ("module", "模块"), ("trigger", "触发方式"),
        ("pre_condition", "前置条件"), ("logic", "处理逻辑"), ("status_change", "状态变化"),
        ("data_change", "数据变化"), ("follow_up", "后续动作"), ("exception", "异常处理"),
        ("impact", "影响范围"),
    ]
    private let fieldLabels: [String: String] = [
        "类型": "ruleType", "模块": "module", "触发方式": "trigger", "前置条件": "preCondition",
        "处理逻辑": "logic", "状态变化": "statusChange", "数据变化": "dataChange",
        "后续动作": "followUp", "异常处理": "exception", "影响范围": "impact",
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(detail.displayName).font(.title3.bold())
                Spacer()
                if !editing {
                    Button("编辑") { beginEditing() }
                }
                Button { dismiss() } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.plain).foregroundStyle(.tertiary)
            }
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if editing {
                        ForEach(editableFields, id: \.0) { key, label in
                            editorField(key, label: label)
                        }
                    } else {
                        field("类型", label(for: detail.ruleType))
                        field("模块", detail.module)
                        field("触发方式", detail.trigger)
                        field("前置条件", detail.preCondition)
                        field("处理逻辑", detail.logic)
                        field("状态变化", detail.statusChange)
                        field("数据变化", detail.dataChange)
                        field("后续动作", detail.followUp)
                        field("异常处理", detail.exception)
                        field("影响范围", detail.impact)
                        field("代码证据", evidenceLocation)
                        if let snippet = detail.evidenceSnippet, !snippet.isEmpty {
                            Text("证据片段").font(.caption.bold()).foregroundStyle(.secondary)
                            Text(snippet)
                                .font(.system(.caption, design: .monospaced))
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 6))
                        }
                    }
                }
                .padding(.vertical, 4)
            }
            HStack {
                TextField("备注（可选）", text: $note)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 240)
                Spacer()
                if editing {
                    Button("取消编辑") { editing = false }
                    Button("保存修改") { Task { await saveModify() } }
                        .buttonStyle(.borderedProminent)
                } else {
                    Button("废弃", role: .destructive) { Task { await onAction("discard", nil) } }
                    Button("确认") { Task { await onAction("confirm", nil) } }
                        .buttonStyle(.borderedProminent)
                }
            }
        }
        .padding(20)
        .frame(width: 640, height: 660)
    }

    private func beginEditing() {
        var map: [String: String] = [:]
        map["name"] = detail.name ?? ""
        map["module"] = detail.module ?? ""
        map["trigger"] = detail.trigger ?? ""
        map["pre_condition"] = detail.preCondition ?? ""
        map["logic"] = detail.logic ?? ""
        map["status_change"] = detail.statusChange ?? ""
        map["data_change"] = detail.dataChange ?? ""
        map["follow_up"] = detail.followUp ?? ""
        map["exception"] = detail.exception ?? ""
        map["impact"] = detail.impact ?? ""
        form = map
        editing = true
    }

    private func saveModify() async {
        var fields: [String: String] = [:]
        for (key, value) in form { fields[key] = value }
        await onAction("modify", fields)
        editing = false
    }

    @ViewBuilder
    private func editorField(_ key: String, label: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label).font(.callout).foregroundStyle(.secondary).frame(width: 70, alignment: .trailing)
            if key == "logic" {
                TextEditor(text: Binding(get: { form[key] ?? "" }, set: { form[key] = $0 }))
                    .font(.callout).frame(height: 70)
                    .border(Color.gray.opacity(0.3))
            } else {
                TextField(label, text: Binding(get: { form[key] ?? "" }, set: { form[key] = $0 }))
                    .textFieldStyle(.roundedBorder)
            }
        }
    }

    private var evidenceLocation: String? {
        guard let file = detail.evidenceFile else { return nil }
        if let lines = detail.evidenceLines, lines.count == 2 {
            return "\(file):\(lines[0])-\(lines[1])"
        }
        return file
    }

    private func label(for type: String) -> String {
        ["validation": "校验", "calculation": "计算", "status": "状态",
         "permission": "权限", "trigger": "自动触发", "exception": "异常", "other": "其他"][type] ?? type
    }

    @ViewBuilder
    private func field(_ label: String, _ value: String?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label).font(.callout).foregroundStyle(.secondary).frame(width: 70, alignment: .trailing)
            Text(value ?? "-").font(.callout).textSelection(.enabled)
            Spacer()
        }
    }
}

import SwiftUI

// MARK: - 业务对象与流程

struct BusinessView: View {
    @EnvironmentObject private var state: AppState
    @State private var objects: [BizObject] = []
    @State private var flows: [BizFlow] = []
    @State private var domains: [Domain] = []

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                domainSection
                objectSection
                flowSection
            }
            .padding(20)
        }
        .task { await load() }
    }

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        domains = (try? await state.api.get([Domain].self, "projects/\(pid)/domains")) ?? []
        objects = (try? await state.api.get([BizObject].self, "projects/\(pid)/biz-objects")) ?? []
        flows = (try? await state.api.get([BizFlow].self, "projects/\(pid)/flows")) ?? []
    }

    private var domainSection: some View {
        CardView("业务域（\(domains.count)）") {
            if domains.isEmpty {
                Text("暂无业务域（AI 标注后生成）").foregroundStyle(.secondary)
            } else {
                FlowLayoutCompat(items: domains.map { domain in
                    (domain.name, domain.confirmed ? Color.green : Color.gray)
                })
            }
        }
    }

    private var objectSection: some View {
        CardView("业务对象（\(objects.count)）") {
            if objects.isEmpty {
                Text("暂无业务对象").foregroundStyle(.secondary)
            } else {
                ForEach(objects) { object in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(object.name).font(.callout.bold())
                            StateTag(text: object.confirmed ? "已确认" : "AI 推断",
                                     color: object.confirmed ? .green : .gray)
                            if let confidence = object.confidence {
                                Text("可信度 \(confidence)%").font(.caption2).foregroundStyle(.tertiary)
                            }
                        }
                        if let desc = object.description, !desc.isEmpty {
                            Text(desc).font(.caption).foregroundStyle(.secondary)
                        }
                        if let actions = object.actions, !actions.isEmpty {
                            Text("业务动作：" + actions.joined(separator: "、"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let tables = object.sourceTables, !tables.isEmpty {
                            Text("来源表：" + tables.prefix(8).joined(separator: "、"))
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    .padding(.vertical, 5)
                    Divider()
                }
            }
        }
    }

    private var flowSection: some View {
        CardView("业务流程（\(flows.count)）") {
            if flows.isEmpty {
                Text("暂无业务流程").foregroundStyle(.secondary)
            } else {
                ForEach(flows) { flow in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(flow.name ?? "(未命名)").font(.callout.bold())
                            StateTag(text: flow.confirmed ? "已确认" : "未确认",
                                     color: flow.confirmed ? .green : .gray)
                            if let runtime = flow.runtimeLevel {
                                StateTag(text: runtime, color: runtime == "R0" ? .gray : .orange)
                            }
                        }
                        if let steps = flow.businessSteps, !steps.isEmpty {
                            stepFlow(steps)
                        }
                        if let tech = flow.techSteps, !tech.isEmpty {
                            Text("技术调用：" + tech.prefix(10).joined(separator: " → "))
                                .font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    .padding(.vertical, 5)
                    Divider()
                }
            }
        }
    }

    private func stepFlow(_ steps: [String]) -> some View {
        HStack(spacing: 4) {
            ForEach(Array(steps.enumerated()), id: \.offset) { index, step in
                if index > 0 {
                    Image(systemName: "arrow.right").font(.caption2).foregroundStyle(.tertiary)
                }
                Text(step).font(.caption)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(.tint.opacity(0.1), in: Capsule())
            }
        }
    }
}

/// 简单换行的标签流（替代 UIKit flow layout）。
struct FlowLayoutCompat: View {
    let items: [(String, Color)]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), alignment: .leading)], alignment: .leading, spacing: 6) {
            ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                StateTag(text: item.0, color: item.1)
            }
        }
    }
}

// MARK: - 业务推演

struct ChainView: View {
    @EnvironmentObject private var state: AppState
    @State private var chain: Chain?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let chain {
                    if chain.hasChain {
                        mainlineSection(chain)
                        if let rels = chain.relationships, !rels.isEmpty {
                            CardView("驱动关系") {
                                ForEach(rels) { relation in
                                    HStack {
                                        Text("\(relation.from) → \(relation.to)").font(.callout)
                                        if let v = relation.verification {
                                            StateTag(text: v, color: .orange)
                                        }
                                        Spacer()
                                        if let text = relation.relation { Text(text).font(.caption).foregroundStyle(.secondary) }
                                    }
                                }
                            }
                        }
                        CardView("设计叙事") {
                            Text(chain.designNarrative ?? "-").font(.callout)
                        }
                        if let assumptions = chain.assumptions, !assumptions.isEmpty {
                            CardView("待确认假设（\(assumptions.count)）") {
                                ForEach(Array(assumptions.enumerated()), id: \.offset) { _, item in
                                    Label(item, systemImage: "questionmark.circle")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    } else {
                        CardView("业务推演") {
                            Text(chain.message ?? "暂无业务推演").foregroundStyle(.secondary)
                        }
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity, minHeight: 200)
                }
            }
            .padding(20)
        }
        .task { await load() }
    }

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        chain = try? await state.api.get(Chain.self, "projects/\(pid)/chain")
    }

    private func mainlineSection(_ chain: Chain) -> some View {
        CardView("业务主线") {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array((chain.mainline ?? []).enumerated()), id: \.offset) { index, node in
                    if index > 0 {
                        HStack(spacing: 0) {
                            Rectangle().fill(Color.accentColor.opacity(0.4)).frame(width: 2, height: 14)
                                .padding(.leading, 11)
                        }
                    }
                    HStack(spacing: 8) {
                        ZStack {
                            Circle().fill(Color.accentColor.opacity(0.15))
                            Text("\(index + 1)").font(.caption.bold()).foregroundStyle(.tint)
                        }
                        .frame(width: 24, height: 24)
                        Text(node).font(.callout.bold())
                    }
                    .padding(.vertical, 2)
                }
            }
            HStack {
                if let confidence = chain.confidence {
                    StateTag(text: "AI 可信度 \(confidence)%", color: .orange)
                }
                if let runtime = chain.runtimeLevel {
                    StateTag(text: "运行验证 \(runtime)", color: runtime == "R0" ? .gray : .orange)
                }
                Spacer()
            }
            .padding(.top, 8)
        }
    }
}

// MARK: - 数据与接口

struct DataView: View {
    @EnvironmentObject private var state: AppState
    @State private var tables: [DataTable] = []
    @State private var selectedTable: DataTable?
    @State private var sqlOps: [SqlOperation] = []
    @State private var entries: [Entry] = []

    var body: some View {
        VStack(spacing: 0) {
            if tables.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "cylinder").font(.system(size: 34)).foregroundStyle(.tertiary)
                    Text("未从代码中提取到数据表").foregroundStyle(.secondary)
                }
                .frame(maxHeight: .infinity)
            } else {
               HSplitView {
                    List(selection: $selectedTable) {
                        ForEach(tables) { table in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(table.name).font(.callout.monospaced())
                                if let comment = table.comment, !comment.isEmpty {
                                    Text(comment).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                }
                            }
                            .tag(table)
                        }
                    }
                    .frame(minWidth: 260)
                    sqlDetail
                        .frame(minWidth: 420)
                }
            }
        }
        .task { await load() }
        .onChange(of: selectedTable) { table in
            Task { await loadSqlOps(table?.name) }
        }
    }

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        tables = (try? await state.api.get([DataTable].self, "projects/\(pid)/tables")) ?? []
        entries = (try? await state.api.get([Entry].self, "projects/\(pid)/objects",
                                            query: ["entry": "true"])) ?? []
        selectedTable = tables.first
    }

    private func loadSqlOps(_ table: String?) async {
        guard let pid = state.currentProject?.id else { return }
        var query: [String: String] = [:]
        if let table { query["table"] = table }
        sqlOps = (try? await state.api.get([SqlOperation].self, "projects/\(pid)/sql-ops", query: query)) ?? []
    }

    private var sqlDetail: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let table = selectedTable {
                    Text("表 \(table.name)").font(.headline)
                    Text("共 \(sqlOps.count) 条 SQL 操作记录").font(.caption).foregroundStyle(.secondary)
                    ForEach(sqlOps) { op in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                StateTag(text: op.op.uppercased(),
                                         color: op.op == "select" ? .blue : op.op == "delete" ? .red : .orange)
                                if let fields = op.fields, !fields.isEmpty {
                                    Text(fields.prefix(6).joined(separator: ", "))
                                        .font(.caption2).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if let file = op.file, let line = op.line {
                                    Text("\(file):\(line)").font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                                }
                            }
                            if let sql = op.sql {
                                Text(sql)
                                    .font(.system(.caption2, design: .monospaced))
                                    .lineLimit(4)
                                    .foregroundStyle(.secondary)
                            }
                        }
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 6))
                    }
                    if !entries.isEmpty {
                        CardView("程序入口（\(entries.count)）") {
                            ForEach(entries.prefix(30)) { entry in
                                HStack {
                                    StateTag(text: entry.entryKind ?? "other", color: .blue)
                                    Text(entry.name).font(.callout)
                                    Spacer()
                                    Text(entry.file ?? "").font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                                }
                            }
                        }
                    }
                }
            }
            .padding(16)
        }
    }
}

// MARK: - 待确认项

struct QuestionsView: View {
    @EnvironmentObject private var state: AppState
    @State private var questions: [Question] = []

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text("AI 提出的待确认问题").font(.headline)
                Spacer()
                Text("共 \(questions.count) 项待处理").foregroundStyle(.secondary)
            }
            .padding(.horizontal, 4)
            if questions.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "checkmark.seal").font(.system(size: 34)).foregroundStyle(.green)
                    Text("没有待确认事项").foregroundStyle(.secondary)
                }
                .frame(maxHeight: .infinity)
            } else {
                List {
                    ForEach(questions) { question in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                if let type = question.qtype {
                                    StateTag(text: type, color: .orange)
                                }
                                Spacer()
                                Button("标记已解决") { Task { await resolve(question.id) } }
                                    .controlSize(.small)
                            }
                            Text(question.desc ?? "").font(.callout)
                            if let suggestion = question.suggestion {
                                Text("建议：\(suggestion)").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .padding(.vertical, 3)
                    }
                }
            }
        }
        .padding(16)
        .task { await load() }
    }

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        questions = (try? await state.api.get([Question].self, "projects/\(pid)/questions",
                                              query: ["status": "open"])) ?? []
    }

    private func resolve(_ id: Int) async {
        guard let pid = state.currentProject?.id else { return }
        try? await state.api.send("projects/\(pid)/questions/\(id)/resolve", method: "POST")
        await load()
    }
}

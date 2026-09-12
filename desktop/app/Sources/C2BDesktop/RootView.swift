import SwiftUI

enum SidebarSection: String, CaseIterable, Identifiable {
    case overview = "项目总览"
    case rules = "流程与规则"
    case business = "业务对象与流程"
    case chain = "业务推演"
    case data = "数据与接口"
    case questions = "待确认项"
    case documents = "文档与交付物"
    case help = "帮助"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .rules: return "checklist"
        case .business: return "shippingbox"
        case .chain: return "arrow.triangle.branch"
        case .data: return "cylinder"
        case .questions: return "questionmark.circle"
        case .documents: return "doc.richtext"
        case .help: return "book"
        }
    }

    static let analysisSections: [SidebarSection] = [.overview, .rules, .business, .chain, .data, .questions]
    static let deliverySections: [SidebarSection] = [.documents, .help]
}

struct RootView: View {
    @EnvironmentObject private var state: AppState
    @State private var section: SidebarSection = .overview
    @State private var importSheet = false
    @State private var confirmDelete = false

    var body: some View {
        Group {
            switch state.phase {
            case .starting(let message):
                startingView(message)
            case .failed(let message):
                failureView(message)
            case .ready:
                mainView()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .c2bNewProject)) { _ in
            importSheet = true
        }
        .onReceive(NotificationCenter.default.publisher(for: .c2bAutoRun)) { _ in
            Task { await state.startAutoRun() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .c2bNavigate)) { note in
            if let target = note.object as? SidebarSection { section = target }
        }
    }

    // MARK: - 启动/失败

    private func startingView(_ message: String) -> some View {
        VStack(spacing: 14) {
            ProgressView().controlSize(.large)
            Text(message).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func failureView(_ message: String) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundStyle(.orange)
            Text("本地分析引擎未能启动").font(.title3.bold())
            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 480)
            Button("重试") { Task { await state.retryStart() } }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 主界面

    private func mainView() -> some View {
        NavigationSplitView {
            sidebar
        } detail: {
            detailArea
        }
        .sheet(isPresented: $importSheet) {
            ImportSheet().environmentObject(state)
        }
        .confirmationDialog(deleteMessage, isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除", role: .destructive) { Task { await state.deleteCurrentProject() } }
        }
        .alert("出错了", isPresented: errorBinding) {
            Button("好的", role: .cancel) {}
        } message: {
            Text(state.lastError ?? "")
        }
    }

    private var deleteMessage: String {
        "删除项目将移除全部图谱数据和系统托管的代码目录，且不可恢复。确定删除？"
    }

    private var sidebar: some View {
        List(selection: $section) {
            Section("分析视图") {
                sidebarItems(SidebarSection.analysisSections)
            }
            Section("交付") {
                sidebarItems(SidebarSection.deliverySections)
            }
            projectMenu
        }
        .listStyle(.sidebar)
        .navigationSplitViewColumnWidth(min: 180, ideal: 200)
    }

    @ViewBuilder
    private func sidebarItems(_ items: [SidebarSection]) -> some View {
        ForEach(items) { item in
            Label(item.rawValue, systemImage: item.icon).tag(item)
        }
    }

    @ViewBuilder
    private var projectMenu: some View {
        if !state.projects.isEmpty {
            Section {
                Menu {
                    ForEach(state.projects) { project in
                        Button(project.id == state.currentProject?.id ? "✓ \(project.name)" : project.name) {
                            state.select(project)
                        }
                    }
                    Divider()
                    Button("删除当前项目…", role: .destructive) { confirmDelete = true }
                } label: {
                    Label(projectMenuTitle, systemImage: "folder")
                }
            }
        }
    }

    private var projectMenuTitle: String {
        state.currentProject.map { "#\($0.id) \($0.name)" } ?? "选择项目"
    }

    private var detailArea: some View {
        detailContent
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    toolbarButtons
                }
            }
            .navigationTitle(section.rawValue)
            .navigationSubtitle(projectMenuTitle)
    }

    private var toolbarButtons: some View {
        HStack {
            Button {
                Task { await state.startAutoRun() }
            } label: {
                Label("一键分析", systemImage: "bolt.fill")
            }
            .disabled(state.runningJob?.status == "running")
            .help("扫描（如需）→ AI 归纳 → 模块分析，一条龙执行")
            Button {
                importSheet = true
            } label: {
                Label("新建项目", systemImage: "plus")
            }
        }
    }

    @ViewBuilder
    private var detailContent: some View {
        if state.currentProject == nil {
            emptyView
        } else {
            switch section {
            case .overview: OverviewView()
            case .rules: RulesView()
            case .business: BusinessView()
            case .chain: ChainView()
            case .data: DataView()
            case .questions: QuestionsView()
            case .documents: DocumentsView()
            case .help: HelpView()
            }
        }
    }

    private var errorBinding: Binding<Bool> {
        Binding(get: { state.lastError != nil }, set: { if !$0 { state.lastError = nil } })
    }

    private var emptyView: some View {
        VStack(spacing: 14) {
            Image(systemName: "sparkles")
                .font(.system(size: 40))
                .foregroundStyle(.tint)
            Text("欢迎使用 C2B Desktop").font(.title3.bold())
            Text("把旧系统代码交给 C2B：自动还原业务流程、业务规则，并生成客户交付总报告")
                .foregroundStyle(.secondary)
            Button("新建项目（导入代码）") { importSheet = true }
                .buttonStyle(.borderedProminent)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

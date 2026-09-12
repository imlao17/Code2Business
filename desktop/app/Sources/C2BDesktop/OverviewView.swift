import SwiftUI

struct OverviewView: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let banner = state.autoBanner {
                    bannerView(banner)
                }
                if let next = state.pipeline?.next {
                    guideBanner(next)
                }
                if let steps = state.pipeline?.steps, !steps.isEmpty {
                    CardView("分析流程") {
                        StepBar(steps: steps)
                    }
                }
                if let overview = overview {
                    scaleCards(overview)
                    graphCards(overview)
                } else {
                    CardView("系统概览") {
                        Text("还没有已完成的扫描。点击右上角「一键分析」开始。")
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .padding(20)
        }
        .task { await load() }
    }

    @State private var overview: Overview?

    private func load() async {
        guard let pid = state.currentProject?.id else { return }
        overview = try? await state.api.get(Overview.self, "projects/\(pid)/overview")
    }

    private func bannerView(_ text: String) -> some View {
        HStack {
            Image(systemName: "info.circle.fill").foregroundStyle(.tint)
            Text(text).font(.callout)
            Spacer()
            Button { state.autoBanner = nil } label: { Image(systemName: "xmark") }
                .buttonStyle(.plain).foregroundStyle(.tertiary)
        }
        .padding(12)
        .background(.tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }

    private func guideBanner(_ next: PipelineNext) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 3) {
                Text(guideTitle(next)).font(.callout.bold())
                Text(guideDesc(next)).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if next.status != "running" {
                Button(guideAction(next)) {
                    Task { await runGuide(next) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(next.status == "pending" && next.key == "confirm")
            }
        }
        .padding(14)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
    }

    private func guideTitle(_ next: PipelineNext) -> String {
        switch next.status {
        case "running": return "正在执行：\(next.label)"
        case "error": return "\(next.label)失败"
        default: return "下一步：\(next.label)"
        }
    }

    private func guideDesc(_ next: PipelineNext) -> String {
        if next.status == "error" { return next.error ?? "请重试" }
        switch next.key {
        case "scan":
            return "代码已导入。点击「一键分析」自动完成扫描与后续 AI 归纳。"
        case "label":
            return "扫描已完成。点击「一键分析」让 AI 自动归纳业务域、对象、规则与模块规格。"
        case "graph":
            return "业务图谱已生成，到「业务对象与流程」「业务推演」查看结果。"
        case "confirm":
            return "到「流程与规则」确认规则（可用按可信度一键确认），确认后才能出正式文档。"
        case "export":
            return "流程已完成，到「文档与交付物」下载《客户交付总报告》。"
        default:
            return ""
        }
    }

    private func guideAction(_ next: PipelineNext) -> String {
        if next.status == "error" { return next.key == "scan" ? "重新扫描" : "重试" }
        switch next.key {
        case "scan": return "一键分析"
        case "label": return "一键分析"
        case "confirm": return "去确认规则"
        case "export": return "去下载报告"
        default: return "查看"
        }
    }

    private func runGuide(_ next: PipelineNext) async {
        switch (next.key, next.status) {
        case ("scan", "error"), ("scan", "pending"):
            await state.startScan()
        case ("label", "pending"), ("label", "error"):
            await state.startAutoRun()
        case ("confirm", _):
            await AppRouter.open(.rules)
        case ("export", _):
            await AppRouter.open(.documents)
        default:
            await state.refreshPipeline()
        }
    }

    private func scaleCards(_ overview: Overview) -> some View {
        CardView("代码规模（\(overview.tier) 档）") {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                StatCard("总代码行数", overview.scale.totalLines ?? 0)
                StatCard("代码文件", overview.scale.totalFiles ?? 0)
                StatCard("模块", overview.stats.modules ?? 0)
                StatCard("类", overview.stats.classes ?? 0)
                StatCard("方法", overview.stats.methods ?? 0)
                StatCard("数据表", overview.stats.dataTables ?? 0)
            }
        }
    }

    private func graphCards(_ overview: Overview) -> some View {
        CardView("业务知识") {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 10)], spacing: 10) {
                StatCard("业务域", overview.stats.domains ?? 0)
                StatCard("业务对象", overview.stats.objects ?? 0)
                StatCard("业务规则", overview.stats.rules ?? 0)
                StatCard("已确认规则", overview.stats.confirmedRules ?? 0)
                StatCard("业务流程", overview.stats.flows ?? 0)
                StatCard("待确认项", overview.stats.questions ?? 0)
            }
        }
    }
}

// MARK: - 共用卡片与路由

struct CardView<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// 页面间导航（总览引导按钮跳转）。
@MainActor
enum AppRouter {
    static func open(_ section: SidebarSection) async {
        NotificationCenter.default.post(name: .c2bNavigate, object: section)
    }
}

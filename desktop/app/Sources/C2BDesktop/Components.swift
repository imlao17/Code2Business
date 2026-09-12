import SwiftUI
import AppKit

// MARK: - 共用小组件

struct StepBar: View {
    let steps: [PipelineStep]

    private func statusColor(_ status: String) -> Color {
        switch status {
        case "done": return .green
        case "running": return .blue
        case "error": return .red
        case "ready": return .mint
        default: return .gray
        }
    }

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Array(steps.enumerated()), id: \.element.key) { index, step in
                if index > 0 {
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 4)
                }
                HStack(spacing: 5) {
                    Group {
                        if step.status == "running" {
                            ProgressView().controlSize(.mini)
                        } else if step.status == "done" {
                            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        } else if step.status == "error" {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                        } else {
                            Image(systemName: "circle").foregroundStyle(.tertiary)
                        }
                    }
                    .font(.caption)
                    Text(step.label).font(.caption)
                    if step.status == "running", let progress = step.progress, let total = step.total, total > 0 {
                        Text("\(progress)/\(total)").font(.caption2).foregroundStyle(.secondary)
                    } else if let count = step.count, step.status == "done" {
                        Text("\(count)").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(statusColor(step.status).opacity(step.status == "pending" ? 0.06 : 0.14),
                            in: Capsule())
            }
        }
    }
}

struct StatCard: View {
    let label: String
    let value: String

    init(_ label: String, _ value: Int) {
        self.label = label
        self.value = value.formatted()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.bold().monospacedDigit())
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
    }
}

struct StateTag: View {
    let text: String
    var color: Color = .secondary

    var body: some View {
        Text(text)
            .font(.caption2.bold())
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }
}

// MARK: - NSSavePanel / NSOpenPanel 桥接

enum PanelHelper {
    static func savePanel(name: String, allowedExtensions: [String]) -> URL? {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = name
        let types = allowedExtensions.compactMap { UTType(filenameExtension: $0) }
        if !types.isEmpty { panel.allowedContentTypes = types }
        return panel.runModal() == .OK ? panel.url : nil
    }

    static func chooseDirectory() -> (path: String, name: String)? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "选择要分析的代码目录"
        guard panel.runModal() == .OK, let url = panel.url else { return nil }
        let name = url.lastPathComponent.isEmpty ? "本地代码库" : url.lastPathComponent
        return (url.path, name)
    }

    static func chooseZip() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.zip]
        panel.message = "选择 zip 代码包"
        guard panel.runModal() == .OK else { return nil }
        return panel.url
    }
}

import UniformTypeIdentifiers

import SwiftUI

@main
struct C2BDesktopApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup("C2B Desktop") {
            RootView()
                .environmentObject(state)
                .frame(minWidth: 1080, minHeight: 720)
                .task { await state.start() }
        }
        .windowStyle(.automatic)
        .defaultSize(width: 1360, height: 900)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("新建项目…") {
                    NotificationCenter.default.post(name: .c2bNewProject, object: nil)
                }
                .keyboardShortcut("n")
                Button("一键分析") {
                    NotificationCenter.default.post(name: .c2bAutoRun, object: nil)
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
            }
        }

        Settings {
            SettingsView()
                .environmentObject(state)
        }
    }
}

extension Notification.Name {
    static let c2bNewProject = Notification.Name("c2bNewProject")
    static let c2bAutoRun = Notification.Name("c2bAutoRun")
    static let c2bNavigate = Notification.Name("c2bNavigate")
}

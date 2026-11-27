import AppKit

/// Legato lives in the menu bar (LSUIElement): no Dock icon, no main window.
@main
enum Main {
    static func main() {
        let args = CommandLine.arguments
        if args.contains("--screen-allowed") { exit(CGPreflightScreenCaptureAccess() ? 0 : 1) }   // PermissionState.probeScreen
        if let i = args.firstIndex(of: "--snapshots"), i + 1 < args.count {
            Snapshots.dirURL = URL(fileURLWithPath: args[i + 1])
            MainActor.assumeIsolated { Snapshots.render(to: Snapshots.dirURL) }
            return
        }
        if let i = args.firstIndex(of: "--capture-check"), i + 1 < args.count {
            let out = URL(fileURLWithPath: args[i + 1])
            Task {
                await CaptureCheck.run(out: out)
                exit(0)
            }
            RunLoop.main.run()
        }
        if let i = args.firstIndex(of: "--focus-check"), i + 2 < args.count {
            // `open -n -g Legato.app --args --focus-check NAME OUT.json`: bring NAME to the front as the engine's
            // focus request does, from the background, and write whether it worked
            let name = args[i + 1], out = URL(fileURLWithPath: args[i + 2])
            NSApplication.shared.setActivationPolicy(.accessory)
            Task { @MainActor in
                let before = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
                let t = Date()
                let ok = await AppFocus.bring(name: name, bundle: nil, url: nil)
                let report: [String: Any] = [
                    "before": before, "ok": ok, "ms": Int(Date().timeIntervalSince(t) * 1000),
                    "after": NSWorkspace.shared.frontmostApplication?.localizedName ?? "", "trusted": AXIsProcessTrusted()]
                try? JSONSerialization.data(withJSONObject: report, options: [.sortedKeys]).write(to: out)
                exit(0)
            }
            NSApplication.shared.run()
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    var controller: AppController!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.mainMenu = Self.editMenu()
        controller = AppController()
        controller.start()
    }

    /// A menu bar app has no menus on screen, but text fields find ⌘X, ⌘C, ⌘V, ⌘A and ⌘Z through the main menu:
    /// without this one, paste does nothing in Settings or the command bar.
    static func editMenu() -> NSMenu {
        let main = NSMenu()
        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Legato", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let windowItem = NSMenuItem()
        let window = NSMenu(title: "Window")
        window.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = window
        main.addItem(windowItem)
        return main
    }

    func applicationWillTerminate(_ notification: Notification) {
        controller?.shutdown()
    }
}

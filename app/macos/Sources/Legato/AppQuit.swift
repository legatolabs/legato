import AppKit
import ApplicationServices

/// Quitting apps for the agent's `quit_apps` tool. `NSRunningApplication.terminate()` asks an app to quit the way the
/// Dock's Quit does: no Apple Event of ours, so no Automation prompt, and nothing blocks when an app has unsaved work.
/// That app stays open with its save panel, which `resolve` answers through Accessibility once the person has chosen.
@MainActor
enum AppQuit {
    /// Apps with a window or a Dock icon, by name, without Legato and Finder.
    static func running() -> [String] {
        let me = Bundle.main.bundleIdentifier
        return NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular && $0.bundleIdentifier != me && $0.bundleIdentifier != "com.apple.finder" }
            .compactMap(\.localizedName)
    }

    /// Ask each app to quit and wait for them. An app still running with a save panel up is `unsaved` as soon as
    /// the panel shows (about a second); one slow to quit gets up to `timeout`.
    static func quit(_ names: [String], timeout: TimeInterval = 8) async -> (quit: [String], unsaved: [String]) {
        let apps = names.compactMap { n in AppFocus.find(name: n, bundle: nil, url: nil).map { (n, $0) } }
        for (_, app) in apps { app.terminate() }
        let t0 = Date()
        var lastLook = Date.distantPast
        while Date().timeIntervalSince(t0) < timeout {
            let open = apps.filter { !$0.1.isTerminated }
            if open.isEmpty { break }
            if Date().timeIntervalSince(t0) > 0.8, Date().timeIntervalSince(lastLook) > 0.3 {
                lastLook = Date()
                if open.allSatisfy({ find(in: $0.1.processIdentifier, titles: discard + keep) != nil }) { break }
            }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        return (apps.filter { $0.1.isTerminated }.map(\.0), apps.filter { !$0.1.isTerminated }.map(\.0))
    }

    /// The buttons that answer a quit's save panel, in the order to try them. macOS 26 says "Discard Changes and Quit"
    /// or "Quit Anyway" for several documents; a single one asks "Don't Save" or "Delete".
    static let discard = ["Discard Changes and Quit", "Quit Anyway", "Don’t Save", "Don't Save", "Delete", "Discard Changes"]
    static let keep = ["Cancel"]

    /// Press `discard` or Cancel on the app's save panels (a panel can lead to the next one), then wait for it to quit.
    /// Whether it quit, and the buttons pressed.
    static func resolve(_ name: String, discard: Bool, timeout: TimeInterval = 3) async -> (quit: Bool, pressed: [String]) {
        guard let app = AppFocus.find(name: name, bundle: nil, url: nil) else { return (true, []) }
        var pressed: [String] = []
        for _ in 0..<6 where !app.isTerminated {
            guard let (button, title) = find(in: app.processIdentifier, titles: discard ? Self.discard : Self.keep) else {
                if !discard { break }
                try? await Task.sleep(nanoseconds: 300_000_000)
                continue
            }
            AXUIElementPerformAction(button, kAXPressAction as CFString)
            pressed.append(title)
            if !discard { break }
            let t0 = Date()
            while Date().timeIntervalSince(t0) < timeout / 3, !app.isTerminated {
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        }
        if discard {
            let t0 = Date()
            while Date().timeIntervalSince(t0) < timeout, !app.isTerminated {
                try? await Task.sleep(nanoseconds: 100_000_000)
            }
        } else if !app.isTerminated {
            _ = await AppFocus.bring(name: name, bundle: nil, url: nil, timeout: 0.5)
        }
        return (app.isTerminated, pressed)
    }

    /// The first button, in `titles` order, in any of the app's windows and their sheets.
    static func find(in pid: pid_t, titles: [String]) -> (AXUIElement, String)? {
        let root = AXUIElementCreateApplication(pid)
        var found: [String: AXUIElement] = [:]
        func walk(_ e: AXUIElement, depth: Int) {
            guard depth < 8 else { return }
            if string(e, kAXRoleAttribute) == kAXButtonRole as String, let t = string(e, kAXTitleAttribute),
               titles.contains(t), found[t] == nil {
                found[t] = e
            }
            for c in children(e) { walk(c, depth: depth + 1) }
        }
        var windows: CFTypeRef?
        AXUIElementCopyAttributeValue(root, kAXWindowsAttribute as CFString, &windows)
        for w in (windows as? [AXUIElement]) ?? [] { walk(w, depth: 0) }
        for t in titles { if let e = found[t] { return (e, t) } }
        return nil
    }

    private static func string(_ e: AXUIElement, _ attr: String) -> String? {
        var v: CFTypeRef?
        return AXUIElementCopyAttributeValue(e, attr as CFString, &v) == .success ? v as? String : nil
    }

    private static func children(_ e: AXUIElement) -> [AXUIElement] {
        var v: CFTypeRef?
        AXUIElementCopyAttributeValue(e, kAXChildrenAttribute as CFString, &v)
        return (v as? [AXUIElement]) ?? []
    }
}

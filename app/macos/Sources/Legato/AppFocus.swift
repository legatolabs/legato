import AppKit
import ApplicationServices

/// Bringing an app the agent opened to the front. `open -a Messages` from the engine (a process in the background)
/// launches or reopens the app, but macOS leaves an app that was already running behind the app in front: on 17 Nov
/// the model then spent a turn clicking Messages in the Dock. Legato has Accessibility access, which may make an app
/// frontmost.
@MainActor
enum AppFocus {
    /// The running app by bundle id, by name ("Messages", "Messages.app", "/Applications/Spotify.app"), or the app that
    /// opens `url` ("spotify:search:...", "https://...").
    static func find(name: String?, bundle: String?, url: String?) -> NSRunningApplication? {
        let apps = NSWorkspace.shared.runningApplications
        if let b = bundle?.lowercased(), !b.isEmpty {
            return apps.first { $0.bundleIdentifier?.lowercased() == b }
        }
        if let n = name, !n.isEmpty {
            let want = ((n as NSString).lastPathComponent as NSString).deletingPathExtension.lowercased()
            return apps.first {
                $0.localizedName?.lowercased() == want
                    || $0.bundleURL?.deletingPathExtension().lastPathComponent.lowercased() == want
            }
        }
        // the app that opens the link's scheme (the rest may hold spaces, which URL() refuses)
        if let u = url, let scheme = u.split(separator: ":").first, let link = URL(string: "\(scheme)://"),
           let appURL = NSWorkspace.shared.urlForApplication(toOpen: link) {
            return apps.first { $0.bundleURL?.standardizedFileURL == appURL.standardizedFileURL }
        }
        return nil
    }

    /// Wait up to `timeout` for the app to be running (it may still be launching), then make it frontmost. Whether it
    /// is in front after.
    static func bring(name: String?, bundle: String?, url: String?, timeout: TimeInterval = 3) async -> Bool {
        let t0 = Date()
        var app: NSRunningApplication?
        while Date().timeIntervalSince(t0) < timeout {
            app = find(name: name, bundle: bundle, url: url)
            if let a = app, a.isFinishedLaunching { break }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        guard let app else { return false }
        func inFront() -> Bool { NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier }
        if inFront() { return true }
        let element = AXUIElementCreateApplication(app.processIdentifier)
        AXUIElementSetAttributeValue(element, kAXFrontmostAttribute as CFString, kCFBooleanTrue)
        app.activate(options: [.activateAllWindows])
        for _ in 0..<20 where !inFront() {
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return inFront()
    }
}

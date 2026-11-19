import AppKit
import SwiftUI

/// `Legato --snapshots DIR` renders the HUD, the command bar and the setup window to PNGs and exits, without showing
/// anything or touching the screen: for checking the design and for the docs.
enum Snapshots {
    @MainActor
    static func render(to dir: URL) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let noop = HUDActions(pause: {}, resume: {}, stop: {}, answer: { _ in }, history: {}, close: {}, settings: {})

        func hud(_ name: String, _ set: (RunModel) -> Void) {
            let m = RunModel()
            m.reset(task: "Find the cheapest flight to Lisbon on Friday and add it to my calendar", id: "x")
            m.started = Date().addingTimeInterval(-42)
            set(m)
            save(HUDView(model: m, actions: noop), name)
        }
        hud("hud-acting") { m in
            m.phase = .acting; m.turn = 6; m.line = "Click · Search flights"
            m.thought = "The results page has loaded. I'll sort by price before choosing."
        }
        hud("hud-thinking") { m in m.phase = .thinking; m.turn = 2; m.line = "Thinking…" }
        hud("hud-paused") { m in
            m.phase = .paused; m.pausedWhy = "You moved the mouse. Resume when you’re ready."
        }
        hud("hud-confirm") { m in
            m.phase = .confirming
            m.question = "Click “Send” in Mail. That sends something."
        }
        hud("hud-done") { m in
            m.phase = .finished; m.status = "done"; m.turn = 14; m.wall = 96; m.cost = 0.041
            m.message = "Added TAP 1123 (Fri 07:40, €84) to your calendar on Friday."
        }
        hud("hud-error") { m in
            m.phase = .finished; m.status = "error"; m.turn = 0
            m.message = "Add your OpenRouter API key in Settings."
        }

        let state = CommandBarState()
        state.context = AX.Context(app: "Safari", window: "Flights to Lisbon — Google Flights", selection: "Fri 21 Nov",
                                   appIcon: NSWorkspace.shared.icon(forFile: "/Applications/Safari.app"))
        state.text = ""
        let bar = CommandBarView(state: state, model: RunModel(), settings: AppSettings.shared, submit: { _ in },
                                 cancel: {}, stop: {}, openSettings: {})
        save(bar, "command-bar")

        let perms = PermissionState()
        perms.granted = [.accessibility: true, .screen: false, .input: false]
        save(OnboardingView(perms: perms, settings: AppSettings.shared, openSettings: {}, relaunch: {}, done: {})
            .background(Color(nsColor: .windowBackgroundColor)), "onboarding")
    }

    @MainActor
    private static func save<V: View>(_ view: V, _ name: String) {
        let r = ImageRenderer(content: view.padding(24).background(Color(white: 0.55)))
        r.scale = 2
        guard let cg = r.cgImage else { print("could not render \(name)"); return }
        let rep = NSBitmapImageRep(cgImage: cg)
        let url = dirURL.appendingPathComponent(name + ".png")
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
        print(url.path)
    }

    static var dirURL = URL(fileURLWithPath: ".")
}

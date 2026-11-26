import AppKit
import ApplicationServices
import ScreenCaptureKit
import Security
import SwiftUI

/// The three macOS permissions Legato needs, read without prompting, and how to ask for each.
enum Permission: String, CaseIterable, Identifiable {
    case accessibility, input, screen              // Screen Recording last: it takes effect after a reopen
    var id: String { rawValue }

    var title: String {
        switch self {
        case .accessibility: return "Accessibility"
        case .screen: return "Screen Recording"
        case .input: return "Input Monitoring"
        }
    }

    var why: String {
        switch self {
        case .accessibility: return "To click and type for you, and to read a button’s label before clicking it."
        case .screen: return "To see the screen. Screenshots go only to the model you set up."
        case .input: return "To notice when you take over, and to open Legato with the Fn key."
        }
    }

    var granted: Bool {
        switch self {
        case .accessibility: return AXIsProcessTrusted()
        case .screen: return CGPreflightScreenCaptureAccess()
        case .input: return CGPreflightListenEventAccess()
        }
    }

    var pane: String {
        switch self {
        case .accessibility: return "Privacy_Accessibility"
        case .screen: return "Privacy_ScreenCapture"
        case .input: return "Privacy_ListenEvent"
        }
    }

    /// The first time, macOS's own prompt (it adds Legato to the list and offers System Settings); after that, the
    /// right pane of System Settings. Never both at once.
    func request() {
        if self == .screen && Signing.team == nil { Signing.restartScreenPrompt() }
        // on recent macOS only a ScreenCaptureKit call puts Legato in the Screen Recording list (and asks)
        if self == .screen { Task { _ = try? await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true) } }
        let key = "asked.\(rawValue)"
        if UserDefaults.standard.bool(forKey: key) { open(); return }
        UserDefaults.standard.set(true, forKey: key)
        switch self {
        case .accessibility:
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
        case .screen:
            _ = CGRequestScreenCaptureAccess()
        case .input:
            _ = CGRequestListenEventAccess()
        }
    }

    var asked: Bool { UserDefaults.standard.bool(forKey: "asked.\(rawValue)") }

    var symbol: String {
        switch self {
        case .accessibility: return "cursorarrow.click.2"
        case .screen: return "eye"
        case .input: return "keyboard"
        }
    }

    var ask: String {
        switch self {
        case .accessibility: return "Let Legato click and type"
        case .screen: return "Let Legato see the screen"
        case .input: return "Let Legato notice your keys"
        }
    }

    func open() {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") else { return }
        // System Settings doesn't reload the Screen Recording list while it's open, so a new entry stays hidden: quit it
        // and open it again on the pane
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: "com.apple.systempreferences")
        guard self == .screen, !running.isEmpty else { NSWorkspace.shared.open(url); return }
        running.forEach { $0.terminate() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSWorkspace.shared.open(url) }
    }

    static var missing: [Permission] { allCases.filter { !$0.granted } }
}

/// How this copy of Legato is signed. A copy someone built has no team id, and an ad-hoc one a new signature each build.
enum Signing {
    static let (team, adhoc): (String?, Bool) = {
        var code: SecCode?, staticCode: SecStaticCode?, info: CFDictionary?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code,
              SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode,
              SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let d = info as? [String: Any] else { return (nil, true) }
        let flags = (d[kSecCodeInfoFlags as String] as? NSNumber)?.uint32Value ?? 0   // 0x2: kSecCodeSignatureAdhoc
        return (d[kSecCodeInfoTeamIdentifier as String] as? String, flags & 0x2 != 0)
    }()

    /// macOS's Screen Recording prompt (universalAccessAuthWarn) runs for weeks and remembers the bundle id it read at
    /// each app path. A copy rebuilt at the same path under another id would have its "Allow" saved for the old id, so it
    /// never shows in System Settings. launchd starts the prompt again on the next request, reading this copy afresh.
    static func restartScreenPrompt() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/killall")
        p.arguments = ["universalAccessAuthWarn"]
        p.standardError = FileHandle.nullDevice
        try? p.run()
        p.waitUntilExit()
    }
}

final class PermissionState: ObservableObject {
    @Published var granted: [Permission: Bool] = [:]
    @Published var asked: Set<Permission> = Set(Permission.allCases.filter(\.asked))   // redraws the step after a click
    @Published var hotkeyTried = false              // Fn or ⌥⇧Space opened the command bar during onboarding
    private var timer: Timer?
    var onChange: (() -> Void)?
    let launchedWithScreen = CGPreflightScreenCaptureAccess()

    func startPolling() {
        refresh()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func stopPolling() { timer?.invalidate(); timer = nil }

    private var screenAllowed = false
    private var screenProbing = false
    private var screenRequested = false             // clicked in this run: the probe may bring up macOS's prompt

    func request(_ p: Permission) {
        p.request()
        asked.insert(p)
        if p == .screen { screenRequested = true }
    }

    func refresh() {
        var g: [Permission: Bool] = [:]
        for p in Permission.allCases { g[p] = p.granted }
        if g[.screen] == false { g[.screen] = screenAllowed; probeScreen() }
        if g != granted {
            granted = g
            onChange?()
        }
    }

    /// CGPreflightScreenCaptureAccess keeps its first answer until Legato restarts, so the step would never see the
    /// switch go on; ScreenCaptureKit asks macOS each time (and doesn't ask the user again once Legato is in the list).
    private func probeScreen() {
        guard screenRequested, !screenProbing, !screenAllowed else { return }
        screenProbing = true
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, _ in
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }
                self.screenProbing = false
                if content != nil { self.screenAllowed = true; self.refresh() }
            }
        }
    }

    /// Screen Recording takes effect only after Legato is reopened.
    var needsRelaunch: Bool { !launchedWithScreen && (granted[.screen] ?? false) }
}

/// First run, one thing at a time: welcome, each missing permission, try Fn, done. Each step moves on by itself once
/// it's done; after the reopen Screen Recording needs, it picks up at "try it".
struct OnboardingView: View {
    enum Step: Equatable { case welcome, permission(Permission), tryIt, done }

    @ObservedObject var perms: PermissionState
    @ObservedObject var settings: AppSettings
    let openSettings: () -> Void
    let relaunch: () -> Void
    let done: () -> Void
    @State private var step: Step

    init(perms: PermissionState, settings: AppSettings, openSettings: @escaping () -> Void,
         relaunch: @escaping () -> Void, done: @escaping () -> Void) {
        self.perms = perms
        self.settings = settings
        self.openSettings = openSettings
        self.relaunch = relaunch
        self.done = done
        let missing = Permission.missing
        if missing.count == Permission.allCases.count && !settings.onboarded {
            _step = State(initialValue: .welcome)
        } else if missing.isEmpty && settings.onboarded {
            _step = State(initialValue: .done)      // Permissions… from the menu with nothing missing
        } else {
            _step = State(initialValue: Self.after(missing))
        }
    }

    /// The name System Settings lists ("Legato", or a test copy's own name).
    static let appName = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? "Legato"

    /// The next step: the first missing permission, or trying Fn.
    static func after(_ missing: [Permission]) -> Step { missing.first.map { .permission($0) } ?? .tryIt }

    /// Fn tapped on its own also opens the emoji picker or switches input source unless set to Do Nothing (0).
    private var fnDoesSomethingElse: Bool {
        (UserDefaults(suiteName: "com.apple.HIToolbox")?.object(forKey: "AppleFnUsageType") as? Int) != 0
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if case .permission = step { progress.padding(.bottom, 18) }
            content
            Spacer(minLength: 16)
            buttons
        }
        .padding(28)
        .frame(width: 440, height: 380)
        .onChange(of: perms.granted) { _, g in
            if case .permission(let p) = step, g[p] == true, !(p == .screen && perms.needsRelaunch) {
                withAnimation { step = Self.after(Permission.missing) }
            }
        }
        .onChange(of: perms.hotkeyTried) { _, tried in
            if tried && step == .tryIt { withAnimation { step = .done } }
        }
    }

    private var progress: some View {
        HStack(spacing: 6) {
            ForEach(Permission.allCases) { p in
                Capsule()
                    .fill(perms.granted[p] == true ? Color.green : (step == .permission(p) ? Color.primary : Color.primary.opacity(0.15)))
                    .frame(height: 3)
            }
        }
    }

    @ViewBuilder private var content: some View {
        switch step {
        case .welcome:
            header(mark: true, "Legato uses your Mac for you",
                   "Tell it a task and it clicks and types in your apps. Move the mouse to take over at any moment. Esc stops it.")
        case .permission(let p):
            header(symbol: p.symbol, p.ask, p.why)
            if p == .screen && perms.needsRelaunch {
                note("arrow.clockwise", "Turned on. Legato reopens to start using it.")
            } else {
                if perms.asked.contains(p) { DragApp().padding(.bottom, 8) }
                note("info.circle", perms.asked.contains(p)
                     ? "Switch on \(Self.appName) in the list System Settings shows. Not there? Drag the icon above into the list, or click + and choose it. This window moves on by itself."
                     : "macOS asks next: choose Open System Settings and switch on \(Self.appName).")
                if p == .screen && Signing.adhoc && perms.asked.contains(p) {
                    note("hammer", "This copy has no signing certificate, so each new build needs this again. On but stuck? Remove it with − and click Open System Settings.")
                }
            }
        case .tryIt:
            header(symbol: "command", "Tap Fn to open Legato",
                   "Tap Fn (🌐) on its own, or press ⌥⇧Space. The command bar opens; type a task and press Return.")
            if settings.fnKey && fnDoesSomethingElse {
                HStack(spacing: 6) {
                    note("exclamationmark.circle", "On this Mac Fn also opens the emoji picker.")
                    Button("Change") {
                        NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension")!)
                    }.buttonStyle(.link).font(.system(size: 11))
                }
            }
            if perms.launchedWithScreen {
                note("info.circle", "If macOS asks to let Legato “bypass the system private window picker”, click Allow.")
            }
        case .done:
            header(symbol: settings.problem == nil ? "checkmark.circle" : "cpu", settings.problem == nil ? "You're set" : "Connect a model",
                   settings.problem.map { "\($0) Your key stays in your Keychain." }
                   ?? "Legato uses \(settings.model) on \(settings.preset.name). Change it any time in Settings.")
        }
    }

    @ViewBuilder private var buttons: some View {
        HStack {
            if step != .done {
                Button("Later", action: done).buttonStyle(.borderless).foregroundColor(.secondary)
            }
            Spacer()
            switch step {
            case .welcome:
                Button("Get started") { withAnimation { step = Self.after(Permission.missing) } }
                    .keyboardShortcut(.defaultAction)
            case .permission(let p):
                if p == .screen && perms.needsRelaunch {
                    Button("Reopen Legato", action: relaunch).keyboardShortcut(.defaultAction)
                } else {
                    Button(perms.asked.contains(p) ? "Open System Settings" : "Allow…") { perms.request(p) }
                        .keyboardShortcut(.defaultAction)
                }
            case .tryIt:
                Button("Skip") { withAnimation { step = .done } }
            case .done:
                if settings.problem != nil { Button("Set up model…", action: openSettings) }
                Button("Done", action: done).keyboardShortcut(.defaultAction)
            }
        }
        .controlSize(.large)
    }

    private func header(mark: Bool = false, symbol: String = "", _ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if mark {
                NoteMark().frame(width: 26, height: 26).padding(8)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Theme.ink))
            } else {
                Image(systemName: symbol).font(.system(size: 28, weight: .light))
            }
            Text(title).font(.system(size: 20, weight: .semibold))
            Text(text).font(.system(size: 13)).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 14)
    }

    private func note(_ symbol: String, _ text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Image(systemName: symbol)
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
        .font(.system(size: 11)).foregroundColor(.secondary)
    }
}

/// Legato's icon, to drag into a System Settings list that doesn't show it.
struct DragApp: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: Bundle.main.bundlePath))
                .resizable().frame(width: 32, height: 32)
                .onDrag { NSItemProvider(object: Bundle.main.bundleURL as NSURL) }
            Text(OnboardingView.appName).font(.system(size: 12, weight: .medium))
        }
        .help("Drag into the list in System Settings")
    }
}

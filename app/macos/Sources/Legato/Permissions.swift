import AppKit
import ApplicationServices
import SwiftUI

/// The three macOS permissions Legato needs, read without prompting, and how to ask for each.
enum Permission: String, CaseIterable, Identifiable {
    case accessibility, screen, input
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

    /// Show the system prompt (first time) and open the right pane of System Settings.
    func request() {
        switch self {
        case .accessibility:
            let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
            _ = AXIsProcessTrustedWithOptions(opts)
        case .screen:
            _ = CGRequestScreenCaptureAccess()
        case .input:
            _ = CGRequestListenEventAccess()
        }
        open()
    }

    func open() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }

    static var missing: [Permission] { allCases.filter { !$0.granted } }
}

final class PermissionState: ObservableObject {
    @Published var granted: [Permission: Bool] = [:]
    private var timer: Timer?
    var onChange: (() -> Void)?
    let launchedWithScreen = CGPreflightScreenCaptureAccess()

    func startPolling() {
        refresh()
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.refresh() }
    }

    func stopPolling() { timer?.invalidate(); timer = nil }

    func refresh() {
        var g: [Permission: Bool] = [:]
        for p in Permission.allCases { g[p] = p.granted }
        if g != granted {
            granted = g
            onChange?()
        }
    }

    /// Screen Recording takes effect only after Legato is reopened.
    var needsRelaunch: Bool { !launchedWithScreen && (granted[.screen] ?? false) }
}

struct OnboardingView: View {
    @ObservedObject var perms: PermissionState
    @ObservedObject var settings: AppSettings
    let openSettings: () -> Void
    let relaunch: () -> Void
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack(spacing: 12) {
                NoteMark().frame(width: 30, height: 30)
                    .padding(8)
                    .background(RoundedRectangle(cornerRadius: 10).fill(Theme.ink))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Set up Legato").font(.system(size: 20, weight: .semibold))
                    Text("Legato works on your Mac with your mouse and keyboard. You can take over at any moment: move the mouse or press a key and it pauses. Esc stops it.")
                        .font(.system(size: 12)).foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            VStack(spacing: 0) {
                ForEach(Permission.allCases) { p in
                    PermissionRow(p: p, granted: perms.granted[p] ?? false)
                    if p != Permission.allCases.last { Divider() }
                }
            }
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.08)))

            if perms.needsRelaunch {
                HStack {
                    Image(systemName: "arrow.clockwise.circle")
                    Text("Reopen Legato so Screen Recording takes effect.")
                    Spacer()
                    Button("Reopen", action: relaunch)
                }
                .font(.system(size: 12))
            }

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Image(systemName: settings.problem == nil ? "checkmark.circle.fill" : "circle")
                        .foregroundColor(settings.problem == nil ? .green : .secondary)
                    Text("Connect a model").font(.system(size: 13, weight: .medium))
                    Spacer()
                    Button(settings.problem == nil ? "Change…" : "Set up…", action: openSettings)
                }
                Text(settings.problem ?? "\(settings.model) on \(settings.preset.name). Your key stays in your Keychain.")
                    .font(.system(size: 11)).foregroundColor(.secondary)
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Open Legato with Fn").font(.system(size: 13, weight: .medium))
                Text("Tap Fn (🌐) on its own, or press ⌥⇧Space. For Fn, set System Settings → Keyboard → “Press 🌐 key to” → Do Nothing, so the emoji picker doesn’t open too.")
                    .font(.system(size: 11)).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Open Keyboard Settings") {
                    NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.Keyboard-Settings.extension")!)
                }.buttonStyle(.link).font(.system(size: 11))
            }

            HStack {
                Spacer()
                Button(Permission.missing.isEmpty && settings.problem == nil ? "Done" : "Later", action: done)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(24)
        .frame(width: 520)
    }
}

struct PermissionRow: View {
    let p: Permission
    let granted: Bool
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: granted ? "checkmark.circle.fill" : "circle")
                .foregroundColor(granted ? .green : .secondary)
                .font(.system(size: 15))
            VStack(alignment: .leading, spacing: 2) {
                Text(p.title).font(.system(size: 13, weight: .medium))
                Text(p.why).font(.system(size: 11)).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
            if !granted {
                Button("Allow…") { p.request() }
            }
        }
        .padding(12)
    }
}

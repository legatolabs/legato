import AppKit
import SwiftUI

/// The command bar: a Spotlight-style panel opened with Fn or ⌥⇧Space. It takes typing without activating Legato, so
/// the app the person was in stays in front, and the agent starts where they were.
final class CommandBarController: NSObject, NSWindowDelegate {
    let panel: KeyablePanel
    let state = CommandBarState()

    init(model: RunModel, settings: AppSettings, submit: @escaping (String, AX.Context) -> Void, stop: @escaping () -> Void,
         openSettings: @escaping () -> Void) {
        panel = KeyablePanel(contentRect: NSRect(x: 0, y: 0, width: 640, height: 120),
                             styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                             backing: .buffered, defer: false)
        super.init()
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .modalPanel
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        panel.sharingType = .none
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.delegate = self
        let view = CommandBarView(state: state, model: model, settings: settings,
                                  submit: { [weak self] text in
                                      guard let self else { return }
                                      let ctx = self.state.context
                                      self.hide()
                                      submit(text, ctx)
                                  },
                                  cancel: { [weak self] in self?.hide() },
                                  stop: stop,
                                  openSettings: { [weak self] in self?.hide(); openSettings() })
        let host = NSHostingView(rootView: view)
        host.sizingOptions = [.intrinsicContentSize]
        panel.contentView = host
    }

    var isVisible: Bool { panel.isVisible }

    func toggle() { panel.isVisible ? hide() : show() }

    func show() {
        state.context = AX.frontContext()
        state.text = ""
        state.focus += 1
        let vis = MainDisplay.screen.visibleFrame
        let size = panel.contentView?.fittingSize ?? NSSize(width: 640, height: 120)
        panel.setFrame(NSRect(x: vis.midX - size.width / 2, y: vis.minY + vis.height * 0.66 - size.height / 2,
                              width: size.width, height: size.height), display: true)
        panel.makeKeyAndOrderFront(nil)
    }

    func hide() { panel.orderOut(nil) }

    func windowDidResignKey(_ notification: Notification) { hide() }
}

final class CommandBarState: ObservableObject {
    @Published var text = ""
    @Published var context = AX.Context()
    @Published var focus = 0
}

struct CommandBarView: View {
    @ObservedObject var state: CommandBarState
    @ObservedObject var model: RunModel
    @ObservedObject var settings: AppSettings
    let submit: (String) -> Void
    let cancel: () -> Void
    let stop: () -> Void
    let openSettings: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if model.active {
                busy
            } else {
                input
                Divider().overlay(Theme.line)
                footer
            }
        }
        .frame(width: 640)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.panelFill))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(Theme.line, lineWidth: 1))
        .foregroundColor(.white)
        .environment(\.colorScheme, .dark)
        .onExitCommand(perform: cancel)
        .onChange(of: state.focus) { focused = true }
        .onAppear { focused = true }
    }

    private var input: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                NoteMark().frame(width: 22, height: 22)
                TextField("Tell Legato what to do…", text: $state.text)
                    .textFieldStyle(.plain)
                    .font(.system(size: 20))
                    .focused($focused)
                    .onSubmit(go)
            }
            if !state.context.app.isEmpty {
                HStack(spacing: 6) {
                    if let icon = state.context.appIcon {
                        Image(nsImage: icon).resizable().frame(width: 14, height: 14)
                    }
                    Text(state.context.window.isEmpty ? state.context.app : "\(state.context.app) — \(state.context.window)")
                        .lineLimit(1)
                    if !state.context.selection.isEmpty {
                        Text("“\(state.context.selection.prefix(60))\(state.context.selection.count > 60 ? "…" : "")”")
                            .lineLimit(1)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(Color.white.opacity(0.08)))
                    }
                }
                .font(.system(size: 11))
                .foregroundColor(Theme.dim)
                .padding(.leading, 34)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if let problem = settings.problem {
                Image(systemName: "exclamationmark.circle").foregroundColor(Theme.neon)
                Text(problem)
                Button("Open Settings", action: openSettings).buttonStyle(.link)
            } else {
                Text(settings.model).lineLimit(1)
                Text("·")
                Text(settings.preset.name)
            }
            Spacer()
            Text("↩ run   esc close")
        }
        .font(.system(size: 11))
        .foregroundColor(Theme.dim)
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
    }

    private var busy: some View {
        HStack(spacing: 12) {
            Pulse(active: model.phase != .paused)
            VStack(alignment: .leading, spacing: 2) {
                Text("Legato is working").font(.system(size: 14, weight: .semibold))
                Text(model.task).font(.system(size: 12)).foregroundColor(Theme.dim).lineLimit(1)
            }
            Spacer()
            PillButton(title: "Stop", action: { cancel(); stop() })
        }
        .padding(16)
    }

    private func go() {
        let t = state.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty, settings.problem == nil else { return }
        submit(t)
    }
}

/// The whole note, in neon on ink.
struct NoteMark: View {
    var body: some View {
        ZStack {
            Ellipse().fill(Theme.neon).frame(width: 22, height: 15).rotationEffect(.degrees(-24))
            Ellipse().fill(Theme.panelFill).frame(width: 8, height: 12).rotationEffect(.degrees(30))
        }
    }
}

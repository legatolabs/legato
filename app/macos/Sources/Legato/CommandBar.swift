import AppKit
import Combine
import SwiftUI

/// The command bar: a Spotlight-style panel opened with Fn or ⌥⇧Space. It takes typing without activating Legato, so
/// the app the person was in stays in front, and the agent starts where they were. ⌘1-⌘4 switch to a recent model,
/// ⌘K (or Tab) searches every model of every provider with a key, ↑ and ↓ bring back earlier tasks.
final class CommandBarController: NSObject, NSWindowDelegate {
    let panel: KeyablePanel
    let state = CommandBarState()
    private let settings: AppSettings
    private var watch: AnyCancellable?
    private var top: CGFloat = 0                     // the panel grows and shrinks downward from here

    init(model: RunModel, settings: AppSettings, submit: @escaping (String, AX.Context) -> Void, stop: @escaping () -> Void,
         openSettings: @escaping () -> Void) {
        self.settings = settings
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
        watch = state.objectWillChange.merge(with: settings.objectWillChange)
            .debounce(for: .milliseconds(10), scheduler: RunLoop.main)
            .sink { [weak self] _ in self?.fit() }
    }

    var isVisible: Bool { panel.isVisible }

    func toggle() { panel.isVisible ? hide() : show() }

    func show() {
        state.context = AX.frontContext()
        state.text = ""
        state.mode = .task
        state.recall = -1
        state.focus += 1
        ModelCatalog.refreshStale(settings)
        let vis = MainDisplay.screen.visibleFrame
        let size = panel.contentView?.fittingSize ?? NSSize(width: 640, height: 120)
        top = vis.minY + vis.height * 0.66 + size.height / 2
        panel.setFrame(NSRect(x: vis.midX - size.width / 2, y: top - size.height, width: size.width, height: size.height),
                       display: true)
        panel.makeKeyAndOrderFront(nil)
    }

    /// Keep the top edge where it was while the list of models comes and goes.
    private func fit() {
        guard panel.isVisible, let host = panel.contentView else { return }
        host.layoutSubtreeIfNeeded()
        let size = host.fittingSize
        guard abs(size.height - panel.frame.height) > 0.5 else { return }
        panel.setFrame(NSRect(x: panel.frame.minX, y: top - size.height, width: size.width, height: size.height), display: true)
    }

    func hide() { panel.orderOut(nil) }

    func windowDidResignKey(_ notification: Notification) { hide() }
}

final class CommandBarState: ObservableObject {
    enum Mode { case task, model }
    @Published var text = ""
    @Published var context = AX.Context()
    @Published var focus = 0
    @Published var mode = Mode.task
    @Published var query = ""
    @Published var pick = 0                         // the highlighted model in the list
    var recall = -1                                 // which past task ↑ brought back (-1: none)
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
            } else if state.mode == .model {
                picker
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
        .onExitCommand(perform: { state.mode == .model ? closePicker() : cancel() })
        .onChange(of: state.focus) { focused = true }
        .onAppear { focused = true }
    }

    // ------------------------------------------------------------ the task
    private var input: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                NoteMark().frame(width: 22, height: 22)
                TextField("Tell Legato what to do…", text: $state.text)
                    .textFieldStyle(.plain)
                    .font(.system(size: 20))
                    .focused($focused)
                    .onSubmit(go)
                    .onKeyPress(.upArrow) { recall(+1) }
                    .onKeyPress(.downArrow) { recall(-1) }
                    .onKeyPress(.tab) { openPicker(); return .handled }
            }
            if !state.context.app.isEmpty {
                HStack(spacing: 6) {
                    if let icon = state.context.appIcon {
                        Image(nsImage: icon).resizable().frame(width: 14, height: 14)
                    }
                    Text(state.context.window.isEmpty ? state.context.app : "\(state.context.app) — \(state.context.window)")
                        .lineLimit(1)
                        .truncationMode(.middle)
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
        HStack(spacing: 6) {
            if let problem = settings.problem {
                Image(systemName: "exclamationmark.circle").foregroundColor(Theme.neon)
                Text(problem).lineLimit(1)
                Button("Settings", action: openSettings).buttonStyle(.link)
                Button("Switch model ⌘K", action: openPicker).buttonStyle(.link)
            } else {
                ForEach(Array(chips.enumerated()), id: \.element.id) { i, c in
                    ModelChip(choice: c, index: i + 1, active: c == settings.current) { settings.select(c) }
                }
                Button(action: openPicker) {
                    HStack(spacing: 4) {
                        Image(systemName: "magnifyingglass")
                        Text("⌘K")
                    }
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().stroke(Theme.line, lineWidth: 1))
                }
                .buttonStyle(.plain)
                .keyboardShortcut("k", modifiers: .command)
                .help("Search every model")
            }
            Spacer(minLength: 8)
            Text("↩ run  ↑ last  esc")
        }
        .font(.system(size: 11))
        .foregroundColor(Theme.dim)
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    /// The recent models shown as chips (⌘1-⌘4), the current one among them.
    private var chips: [ModelChoice] {
        var out = Array(settings.recents.filter { settings.usable($0.provider) }.prefix(4))
        if !out.contains(settings.current) && !settings.current.model.isEmpty {
            out = [settings.current] + out.prefix(3)
        }
        return out
    }

    // ------------------------------------------------------------ switching the model
    private var picker: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "cpu").font(.system(size: 17)).foregroundColor(Theme.neon).frame(width: 22)
                TextField("Switch model…", text: $state.query)
                    .textFieldStyle(.plain)
                    .font(.system(size: 20))
                    .focused($focused)
                    .onSubmit { choose(at: state.pick) }
                    .onKeyPress(.upArrow) { move(-1) }
                    .onKeyPress(.downArrow) { move(+1) }
                    .onKeyPress(.tab) { closePicker(); return .handled }
                    .onChange(of: state.query) { state.pick = 0 }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            Divider().overlay(Theme.line)
            let ms = matches
            if ms.isEmpty {
                HStack {
                    Text(settings.choices.isEmpty ? "No provider has a key yet." : "No model matches “\(state.query)”.")
                    Spacer()
                    Button("Add a provider in Settings", action: openSettings).buttonStyle(.link)
                }
                .font(.system(size: 12))
                .foregroundColor(Theme.dim)
                .padding(16)
            } else {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Array(ms.prefix(8).enumerated()), id: \.element.id) { i, c in
                        Button(action: { choose(at: i) }) {
                            HStack(spacing: 10) {
                                Image(systemName: c == settings.current ? "checkmark" : "circle")
                                    .font(.system(size: 10, weight: .bold))
                                    .foregroundColor(c == settings.current ? Theme.neon : .clear)
                                    .frame(width: 12)
                                Text(c.label).font(.system(size: 13, weight: .medium)).lineLimit(1)
                                Text(c.preset.name).font(.system(size: 11)).foregroundColor(Theme.dim)
                                Spacer()
                                if settings.recents.contains(c) {
                                    Text("recent").font(.system(size: 10)).foregroundColor(Theme.dim)
                                }
                            }
                            .padding(.horizontal, 12)
                            .padding(.vertical, 6)
                            .background(RoundedRectangle(cornerRadius: 8)
                                .fill(i == state.pick ? Color.white.opacity(0.10) : .clear))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                    if ms.count > 8 {
                        Text("\(ms.count - 8) more: keep typing").font(.system(size: 11)).foregroundColor(Theme.dim)
                            .padding(.horizontal, 12).padding(.top, 4)
                    }
                }
                .padding(8)
            }
            Divider().overlay(Theme.line)
            HStack {
                Text("↑↓ choose  ↩ switch  esc back")
                Spacer()
                Button("Providers and keys…", action: openSettings).buttonStyle(.link)
            }
            .font(.system(size: 11))
            .foregroundColor(Theme.dim)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
        }
    }

    /// Every word typed appears in the model or its provider's name.
    private var matches: [ModelChoice] {
        let words = state.query.lowercased().split(separator: " ").map(String.init)
        return settings.choices.filter { c in
            let hay = (c.model + " " + c.preset.name).lowercased()
            return words.allSatisfy { hay.contains($0) }
        }
    }

    private func openPicker() {
        state.query = ""
        state.pick = 0
        state.mode = .model
        state.focus += 1
    }

    private func closePicker() {
        state.mode = .task
        state.focus += 1
    }

    private func choose(at i: Int) {
        let ms = Array(matches.prefix(8))
        guard ms.indices.contains(i) else { return }
        settings.select(ms[i])
        closePicker()
    }

    private func move(_ d: Int) -> KeyPress.Result {
        let n = min(8, matches.count)
        guard n > 0 else { return .handled }
        state.pick = (state.pick + d + n) % n
        return .handled
    }

    /// ↑ (+1) brings back the task before, ↓ (-1) the one after; past the newest, the field is empty again.
    private func recall(_ d: Int) -> KeyPress.Result {
        let past = settings.pastTasks
        guard !past.isEmpty, state.text.isEmpty || state.recall >= 0 else { return .ignored }
        let i = state.recall + d
        if i < 0 {
            state.recall = -1
            state.text = ""
        } else if i < past.count {
            state.recall = i
            state.text = past[i]
        }
        return .handled
    }

    // ------------------------------------------------------------ running
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
        settings.noteTask(t)
        settings.noteUsed()
        submit(t)
    }
}

/// A recent model in the footer: ⌘n switches to it.
struct ModelChip: View {
    let choice: ModelChoice
    let index: Int
    let active: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Text("⌘\(index)").foregroundColor(active ? Theme.ink.opacity(0.6) : Theme.dim.opacity(0.7))
                Text(choice.label).lineLimit(1)
            }
            .font(.system(size: 11, weight: active ? .semibold : .regular))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(active ? Theme.neon : Color.white.opacity(0.07)))
            .foregroundColor(active ? Theme.ink : .white.opacity(0.85))
        }
        .buttonStyle(.plain)
        .keyboardShortcut(KeyEquivalent(Character(String(index))), modifiers: .command)
        .help("\(choice.model) · \(choice.preset.name)")
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

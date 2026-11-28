import AppKit
import Combine
import SwiftUI

/// The status pill: a small floating panel showing what the agent is doing, with Pause and Stop, the yes/no
/// questions, and the result at the end. It never takes the focus, is left out of screenshots, and moves out of the
/// way when the agent is about to click under it.
final class HUDController {
    let panel: NSPanel
    private let model: RunModel
    private var atTop = false
    private var hideWork: DispatchWorkItem?
    private var watch: AnyCancellable?
    static let width: CGFloat = 460
    static let wideWidth: CGFloat = 600             // an answer to read (a summary, a list)

    init(model: RunModel, actions: HUDActions) {
        self.model = model
        panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: HUDController.width, height: 64),
                        styleMask: [.nonactivatingPanel, .borderless], backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .statusBar
        panel.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        panel.sharingType = .none
        panel.isMovableByWindowBackground = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        let host = FirstMouseHostingView(rootView: HUDView(model: model, actions: actions))
        host.sizingOptions = [.intrinsicContentSize]
        panel.contentView = host
        watch = model.objectWillChange
            .debounce(for: .milliseconds(30), scheduler: RunLoop.main)
            .sink { [weak self] _ in
                guard let self, self.panel.isVisible else { return }
                self.place()
            }
    }

    func show() {
        hideWork?.cancel()
        place()
        panel.orderFrontRegardless()
    }

    func hide(after delay: TimeInterval = 0) {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.panel.orderOut(nil) }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    func keepOpen() { hideWork?.cancel() }

    private func place() {
        let vis = MainDisplay.screen.visibleFrame
        panel.contentView?.layoutSubtreeIfNeeded()
        let size = panel.contentView?.fittingSize ?? NSSize(width: HUDController.width, height: 64)
        let x = vis.midX - size.width / 2
        let y = atTop ? vis.maxY - size.height - 12 : vis.minY + 18
        panel.setFrame(NSRect(x: x, y: y, width: size.width, height: size.height), display: true)
    }

    /// Move to the other edge if `p` (engine coordinates) is under or near the pill.
    func avoid(_ p: CGPoint) {
        guard panel.isVisible else { return }
        let r = MainDisplay.rectFromAppKit(panel.frame).insetBy(dx: -40, dy: -40)
        if r.contains(p) {
            atTop.toggle()
            place()
        }
    }
}

struct HUDActions {
    var pause: () -> Void
    var resume: () -> Void
    var stop: () -> Void
    var answer: (Bool) -> Void
    var allowForTask: () -> Void = {}
    var history: () -> Void
    var close: () -> Void
    var settings: () -> Void
}

struct HUDView: View {
    @ObservedObject var model: RunModel
    let actions: HUDActions

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            switch model.phase {
            case .confirming: confirm
            case .finished: finished
            default: running
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .frame(width: wide ? HUDController.wideWidth : HUDController.width, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Theme.panelFill))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).stroke(Theme.line, lineWidth: 1))
        .foregroundColor(.white)
        .environment(\.colorScheme, .dark)
        .fixedSize(horizontal: false, vertical: true)
    }

    private var running: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Pulse(active: model.phase != .paused)
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.phase == .paused ? "Paused" : model.stopping ? "Stopping…" : model.line)
                        .font(.system(size: 13, weight: .semibold))
                        .lineLimit(1)
                    Text(model.phase == .paused ? model.pausedWhy : model.task)
                        .font(.system(size: 11))
                        .foregroundColor(Theme.dim)
                        .lineLimit(model.phase == .paused ? 2 : 1)
                }
                Spacer(minLength: 8)
                Clock(model: model)
                if model.phase == .paused {
                    PillButton(title: "Resume", primary: true, action: actions.resume)
                } else {
                    PillButton(title: "Pause", action: actions.pause)
                }
                PillButton(title: "Stop", action: actions.stop)
            }
            if !model.routine.isEmpty && model.phase != .paused {
                Label(model.routine, systemImage: "arrow.clockwise")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundColor(Theme.neon)
                    .padding(.leading, 20)
            }
            if !model.feed.isEmpty && model.phase != .paused {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(model.feed.suffix(3).enumerated()), id: \.offset) { _, line in
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark").font(.system(size: 8, weight: .bold)).foregroundColor(Theme.neon.opacity(0.8))
                            Text(line).lineLimit(1).truncationMode(.tail)
                        }
                    }
                }
                .font(.system(size: 11))
                .foregroundColor(Theme.dim)
                .padding(.leading, 20)
                .transition(.opacity)
            } else if !model.thought.isEmpty && model.phase != .paused {
                Text(model.thought)
                    .font(.system(size: 11))
                    .foregroundColor(Theme.dim)
                    .lineLimit(2)
                    .padding(.leading, 20)
            }
        }
        .animation(.easeOut(duration: 0.15), value: model.feed.count)
    }

    private var confirm: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "hand.raised.fill").foregroundColor(Theme.neon)
                Text(model.askTitle).font(.system(size: 13, weight: .semibold))
            }
            Text(model.question)
                .font(.system(size: 12))
                .foregroundColor(.white.opacity(0.85))
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                PillButton(title: "Stop", action: actions.stop)
                PillButton(title: model.noLabel, action: { actions.answer(false) })
                if model.canAllowForTask {
                    PillButton(title: "Allow for this task", action: actions.allowForTask)
                }
                PillButton(title: model.yesLabel, primary: true, action: { actions.answer(true) })
            }
        }
    }

    private var finished: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Image(systemName: model.status == "done" ? "checkmark.circle.fill" :
                        model.status == "stopped" ? "stop.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundColor(model.status == "done" ? Theme.neon : .white.opacity(0.8))
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.status == "done" && model.wall > 0 ? "Done in \(formatSpeed(model.wall))" : model.headline)
                        .font(.system(size: 13, weight: .semibold))
                    Text(summary).font(.system(size: 11)).foregroundColor(Theme.dim).lineLimit(1)
                }
                Spacer()
                if model.status == "error" && model.message.contains("Settings") {
                    PillButton(title: "Settings", action: actions.settings)
                }
                if !model.runID.isEmpty && model.turn > 0 {
                    PillButton(title: "Details", action: actions.history)
                }
                Button(action: actions.close) {
                    Image(systemName: "xmark").font(.system(size: 10, weight: .bold)).foregroundColor(Theme.dim)
                }.buttonStyle(.plain)
            }
            if !answer.isEmpty {
                if wide {
                    ScrollView {
                        Text(rendered)
                            .font(.system(size: 12.5))
                            .foregroundColor(.white.opacity(0.9))
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.trailing, 6)
                    }
                    .frame(height: min(360, 18 * CGFloat(answer.split(separator: "\n").count + 2)))
                    HStack {
                        Spacer()
                        PillButton(title: copied ? "Copied" : "Copy", action: copy)
                    }
                } else {
                    Text(rendered)
                        .font(.system(size: 12))
                        .foregroundColor(.white.opacity(0.85))
                        .lineLimit(4)
                        .textSelection(.enabled)
                }
            }
        }
    }

    @State private var copied = false

    /// The model's last words without a stray `{"action": "done"}` some models print after them.
    private var answer: String {
        model.message
            .replacingOccurrences(of: "\\{\\s*\"action\"\\s*:\\s*\"(done|fail)\"[^}]*\\}", with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Long answers (several lines, or a few hundred characters) get the wide card with room to read.
    private var wide: Bool {
        model.phase == .finished && (answer.count > 220 || answer.split(separator: "\n").count > 3)
    }

    /// Markdown as the model wrote it (bold, lists, links, code), line breaks kept.
    private var rendered: AttributedString {
        let opts = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        let text = answer.replacingOccurrences(of: "(?m)^#{1,6}\\s*", with: "", options: .regularExpression)
        return (try? AttributedString(markdown: text, options: opts)) ?? AttributedString(text)
    }

    private func copy() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(answer, forType: .string)
        copied = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { copied = false }
    }

    private var summary: String {
        var parts = [model.task]
        if model.wall > 0 && model.status != "done" { parts.append(formatSpeed(model.wall)) }
        if model.turn > 0 { parts.append("\(model.turn) turn\(model.turn == 1 ? "" : "s")") }
        let c = formatCost(model.cost)
        if !c.isEmpty { parts.append(c) }
        return parts.joined(separator: " · ")
    }
}

struct Clock: View {
    @ObservedObject var model: RunModel
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            Text(model.started.map { formatDuration(Date().timeIntervalSince($0)) } ?? "")
                .font(.system(size: 11).monospacedDigit())
                .foregroundColor(Theme.dim)
        }
    }
}

struct Pulse: View {
    let active: Bool
    @State private var on = false
    var body: some View {
        Circle()
            .fill(active ? Theme.neon : Color.white.opacity(0.5))
            .frame(width: 9, height: 9)
            .opacity(active && on ? 0.35 : 1)
            .animation(active ? .easeInOut(duration: 0.8).repeatForever(autoreverses: true) : .default, value: on)
            .onAppear { on = true }
    }
}

struct PillButton: View {
    let title: String
    var primary = false
    let action: () -> Void
    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 12, weight: .semibold))
                .padding(.horizontal, 11)
                .padding(.vertical, 5)
                .background(Capsule().fill(primary ? Theme.neon : Color.white.opacity(0.10)))
                .foregroundColor(primary ? Theme.ink : .white)
        }
        .buttonStyle(.plain)
    }
}

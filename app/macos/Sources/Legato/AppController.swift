import AppKit
import SwiftUI

/// Ties the pieces together: the menu bar item, the command bar, the HUD, the ghost cursor, the input monitor and the
/// engine. Every engine request is answered here: screenshots, the go-ahead before each action, yes/no questions.
final class AppController: NSObject {
    let settings = AppSettings.shared
    let model = RunModel()
    let test = TestState()
    let perms = PermissionState()
    let engine = EngineClient()
    let capturer = ScreenCapturer()
    let monitor = InputMonitor()
    let hotkey = HotKey()
    lazy var ghost = GhostCursor()
    lazy var history = HistoryState(base: settings.runsDir)
    var hud: HUDController!
    var bar: CommandBarController!
    var statusItem: NSStatusItem!

    private var windows: [String: NSWindow] = [:]
    private var heldWillAct: Int?                  // a go-ahead held while the run is paused
    private var pendingAsk: (id: Int, kind: AskKind)?
    private var pausedSincePlan = false            // the model's plan was made before a pause: look again first
    private var pausedWhyNote = ""
    private var watchFrom = Date.distantPast
    private var stopFallback: DispatchWorkItem?

    enum AskKind { case click, engine }

    // ------------------------------------------------------------ start
    func start() {
        hud = HUDController(model: model, actions: HUDActions(
            pause: { [weak self] in self?.pause(what: "paused the run") },
            resume: { [weak self] in self?.resume() },
            stop: { [weak self] in self?.stop() },
            answer: { [weak self] yes in self?.answer(yes) },
            history: { [weak self] in self?.showHistory(select: self?.model.runID) },
            close: { [weak self] in self?.hud.hide(); self?.model.phase = .idle; self?.refreshMenu() },
            settings: { [weak self] in self?.showSettings() }))
        bar = CommandBarController(model: model, settings: settings,
                                   submit: { [weak self] text, ctx in self?.startRun(task: text, context: ctx) },
                                   stop: { [weak self] in self?.stop() },
                                   openSettings: { [weak self] in self?.showSettings() })
        _ = ghost                                   // a window of ours exists before the first screenshot filter

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = MenuBarIcon.image(.idle)
        statusItem.button?.toolTip = "Legato"
        refreshMenu()

        monitor.onTakeover = { [weak self] why in self?.takeover(why) }
        monitor.onEscape = { [weak self] in
            guard let self, self.model.active else { return }
            self.stop()
        }
        monitor.onFnTap = { [weak self] in
            guard let self, self.settings.fnKey else { return }
            self.toggleCommandBar()
        }
        monitor.install()
        hotkey.onPress = { [weak self] in self?.toggleCommandBar() }
        hotkey.register()

        engine.onMessage = { [weak self] msg in self?.handle(msg) }
        engine.onExit = { [weak self] why in self?.engineExited(why) }
        try? engine.start()

        perms.onChange = { [weak self] in
            self?.monitor.install()                 // Input Monitoring may just have been allowed
            if let c = self?.capturer { Task { await c.invalidate() } }
        }
        if !settings.onboarded || !Permission.missing.isEmpty || settings.problem != nil {
            showOnboarding()
        }
    }

    func shutdown() {
        if model.active { stop() }
        engine.stop()
    }

    // ------------------------------------------------------------ menu bar
    func refreshMenu() {
        let menu = NSMenu()
        if model.active {
            let t = NSMenuItem(title: "Working: \(model.task.prefix(48))", action: nil, keyEquivalent: "")
            t.isEnabled = false
            menu.addItem(t)
            if model.phase == .paused {
                menu.addItem(item("Resume", #selector(menuResume)))
            } else {
                menu.addItem(item("Pause", #selector(menuPause)))
            }
            menu.addItem(item("Stop", #selector(menuStop)))
        } else {
            let n = item("New Task…", #selector(menuNewTask))
            n.keyEquivalent = " "
            n.keyEquivalentModifierMask = [.option, .shift]
            menu.addItem(n)
        }
        menu.addItem(.separator())
        menu.addItem(item("History…", #selector(menuHistory)))
        menu.addItem(item("Settings…", #selector(menuSettings), key: ","))
        let missing = Permission.missing
        menu.addItem(item(missing.isEmpty ? "Permissions…" : "Permissions… (\(missing.count) missing)", #selector(menuOnboarding)))
        menu.addItem(.separator())
        menu.addItem(item("Quit Legato", #selector(menuQuit), key: "q"))
        statusItem.menu = menu
        let icon: MenuBarIcon.State
        switch model.phase {
        case .paused: icon = .paused
        case .confirming: icon = .attention
        case .starting, .thinking, .acting: icon = .running
        default: icon = missing.isEmpty ? .idle : .attention
        }
        statusItem.button?.image = MenuBarIcon.image(icon)
    }

    private func item(_ title: String, _ sel: Selector, key: String = "") -> NSMenuItem {
        let i = NSMenuItem(title: title, action: sel, keyEquivalent: key)
        i.target = self
        return i
    }

    @objc private func menuNewTask() { bar.show() }
    @objc private func menuPause() { pause(what: "paused the run") }
    @objc private func menuResume() { resume() }
    @objc private func menuStop() { stop() }
    @objc private func menuHistory() { showHistory(select: nil) }
    @objc private func menuSettings() { showSettings() }
    @objc private func menuOnboarding() { showOnboarding() }
    @objc private func menuQuit() { NSApp.terminate(nil) }

    func toggleCommandBar() {
        if model.phase == .finished { hud.hide(); model.phase = .idle }
        bar.toggle()
    }

    // ------------------------------------------------------------ a run
    func startRun(task: String, context: AX.Context) {
        if model.active { return }
        if let problem = settings.problem {
            finishWithError(task: task, problem)
            showSettings()
            return
        }
        if !Permission.screen.granted || !Permission.accessibility.granted {
            finishWithError(task: task, "Legato needs Accessibility and Screen Recording first. Open Permissions… in the menu bar.")
            showOnboarding()
            return
        }
        if !engine.running {
            do { try engine.start() } catch {
                finishWithError(task: task, error.localizedDescription + " (Settings → Advanced)")
                return
            }
        }
        let id = Self.runID()
        model.reset(task: task, id: id)
        heldWillAct = nil
        pendingAsk = nil
        pausedSincePlan = false
        Task { await capturer.invalidate() }
        monitor.install()
        monitor.resetMovement()
        monitor.watching = true
        watchFrom = Date().addingTimeInterval(0.8)      // the hand that just pressed Return is not a takeover
        hud.show()
        refreshMenu()
        engine.send(["type": "run", "id": id, "task": task, "context": context.summary, "model": settings.model,
                     "base_url": settings.baseURL, "api_key": settings.apiKey, "max_steps": settings.maxSteps, "image_max": settings.imageMax, "wait_scope": "screen", "shell": settings.allowShell,
                     "runs_dir": settings.runsDir.path])
    }

    static func runID() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyyMMdd-HHmmss"
        return f.string(from: Date()) + "-" + UUID().uuidString.prefix(4).lowercased()
    }

    private func finishWithError(task: String, _ message: String) {
        model.reset(task: task, id: "")
        model.phase = .finished
        model.status = "error"
        model.message = message
        hud.show()
        refreshMenu()
    }

    /// `what` finishes "you …" for the person and "the person …" for the model: "moved the mouse", "paused the run".
    func pause(what: String) {
        guard model.active, model.phase != .paused else { return }
        if model.phase == .confirming { return }        // a question is already waiting for the person
        model.phase = .paused
        model.pausedWhy = "You \(what). Resume when you’re ready."
        pausedSincePlan = true
        pausedWhyNote = "The person \(what)."
        ghost.say("Paused")
        refreshMenu()
    }

    private func takeover(_ why: String) {
        guard Date() > watchFrom, [.thinking, .acting, .starting].contains(model.phase) else { return }
        pause(what: why)
    }

    func resume() {
        guard model.phase == .paused else { return }
        model.phase = .thinking
        model.line = "Looking at the screen again…"
        monitor.resetMovement()
        watchFrom = Date().addingTimeInterval(0.5)
        if let id = heldWillAct {
            heldWillAct = nil
            replan(id)
        }
        refreshMenu()
    }

    private func replan(_ id: Int) {
        pausedSincePlan = false
        engine.reply(id, ["replan": pausedWhyNote.isEmpty ? "The person paused the run." : pausedWhyNote])
    }

    func stop() {
        guard model.active else { return }
        model.stopping = true
        if let id = heldWillAct { engine.reply(id, ["stop": "stopped from the Legato app"]); heldWillAct = nil }
        if let ask = pendingAsk { engine.reply(ask.id, ["stop": "stopped from the Legato app"]); pendingAsk = nil }
        if model.phase == .paused || model.phase == .confirming { model.phase = .thinking }
        engine.send(["type": "stop"])
        monitor.watching = false
        stopFallback?.cancel()
        let work = DispatchWorkItem { [weak self] in                 // the engine went quiet: end the run here
            guard let self, self.model.active else { return }
            self.finished(["status": "stopped", "message": "Stopped.", "steps": self.model.turn])
        }
        stopFallback = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: work)
    }

    private func answer(_ yes: Bool) {
        guard let ask = pendingAsk else { return }
        pendingAsk = nil
        model.phase = .acting
        switch ask.kind {
        case .click:
            if yes { go(ask.id, line: model.line) } else {
                engine.reply(ask.id, ["deny": model.question])
                model.phase = .thinking
                model.line = "Not allowed. Thinking again…"
            }
        case .engine:
            if yes { monitor.beginActing() }
            engine.reply(ask.id, ["allow": yes])
        }
        model.question = ""
        refreshMenu()
    }

    private func ask(_ id: Int, kind: AskKind, question: String) {
        pendingAsk = (id, kind)
        model.phase = .confirming
        model.question = question
        hud.show()
        NSSound(named: "Tink")?.play()
        refreshMenu()
    }

    // ------------------------------------------------------------ the engine
    private func handle(_ msg: [String: Any]) {
        switch msg["type"] as? String {
        case "ready":
            monitor.enginePid = pid_t(msg["pid"] as? Int ?? Int(engine.pid))
        case "event":
            event(msg)
        case "request":
            request(msg)
        case "notify":
            if msg["method"] as? String == "acted" { monitor.endActing() }
        default:
            break
        }
    }

    private func event(_ m: [String: Any]) {
        let name = m["event"] as? String ?? ""
        if name == "test_result" {
            test.running = false
            test.ok = m["ok"] as? Bool ?? false
            if test.ok {
                let sees = m["sees_images"] as? Bool ?? false
                test.result = String(format: "Answered in %.1f s", m["seconds"] as? Double ?? 0)
                    + (sees ? " and reads images." : ". It may not read images (it said “\(m["reply"] as? String ?? "")”).")
            } else {
                test.result = m["error"] as? String ?? "failed"
            }
            return
        }
        guard (m["id"] as? String) == model.runID || name == "finished" else { return }
        switch name {
        case "run_started":
            model.phase = .thinking
            model.line = "Looking at the screen…"
        case "thinking":
            model.turn = m["turn"] as? Int ?? model.turn
            if model.phase != .paused && model.phase != .confirming {
                model.phase = .thinking
                model.line = "Thinking…"
            }
        case "plan":
            let text = (m["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if !text.isEmpty { model.thought = text.replacingOccurrences(of: "\n", with: " ") }
            if let u = m["usage"] as? [String: Any] { model.cost = u["cost_usd"] as? Double ?? model.cost }
        case "step":
            model.turn = m["turn"] as? Int ?? model.turn
            if let u = m["usage"] as? [String: Any] { model.cost = u["cost_usd"] as? Double ?? model.cost }
        case "finished":
            if let id = m["id"] as? String, id != model.runID, !model.runID.isEmpty { return }
            finished(m)
        default:
            break
        }
    }

    private func finished(_ m: [String: Any]) {
        stopFallback?.cancel()
        monitor.watching = false
        heldWillAct = nil
        pendingAsk = nil
        model.phase = .finished
        model.stopping = false
        model.status = m["status"] as? String ?? "error"
        let err = m["error"] as? String ?? ""
        let msg = (m["message"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        model.message = model.status == "error" ? (err.isEmpty ? msg : err) : msg
        if model.status == "max_steps" && model.message.isEmpty {
            model.message = "It used all \(settings.maxSteps) turns. You can allow more in Settings."
        }
        model.turn = m["steps"] as? Int ?? model.turn
        model.wall = m["wall_s"] as? Double ?? model.started.map { Date().timeIntervalSince($0) } ?? 0
        if let u = m["usage"] as? [String: Any] { model.cost = u["cost_usd"] as? Double ?? model.cost }
        ghost.hide()
        hud.show()
        let long = model.message.count > 220 || model.message.split(separator: "\n").count > 3
        if model.status == "done" && !long { hud.hide(after: 12) }   // an answer to read stays until closed
        refreshMenu()
        if windows["history"]?.isVisible == true { history.reload(select: model.runID) }
    }

    private func engineExited(_ why: String) {
        monitor.enginePid = 0
        if model.active {
            finished(["status": "error", "error": why + " See Settings → Advanced → Show engine log."])
        }
    }

    private func request(_ m: [String: Any]) {
        guard let id = m["id"] as? Int, let method = m["method"] as? String else { return }
        switch method {
        case "capture":
            let kind = m["kind"] as? String ?? "png"
            let s = m["size"] as? [Double] ?? []
            let size = s.count == 2 ? CGSize(width: s[0], height: s[1]) : MainDisplay.frame.size
            let front = (m["scope"] as? String) == "app" ? NSWorkspace.shared.frontmostApplication?.processIdentifier : nil
            let capturer = self.capturer
            Task.detached {
                do {
                    if kind == "digest" {
                        let img = try await capturer.image(size: size, frontPid: front)
                        let d = ScreenCapturer.md5(img)
                        // the whole screen also comes as a grid of cells, so the wait can leave out what moves on its own
                        let cells = front == nil ? ScreenCapturer.cellDigests(img, cell: ScreenCapturer.cell) : []
                        await MainActor.run { self.engine.reply(id, ["digest": d, "cells": cells]) }
                    } else {
                        let (url, got) = try await capturer.png(size: size)
                        await MainActor.run {
                            self.engine.reply(id, ["path": url.path, "size": [Int(got.width), Int(got.height)]])
                        }
                    }
                } catch {
                    let granted = CGPreflightScreenCaptureAccess()
                    let text = granted ? error.localizedDescription
                        : "Screen Recording is off for Legato (Permissions… in the menu bar)."
                    await MainActor.run { self.engine.reply(id, ["error": text]) }
                }
            }
        case "focus":
            // an app the agent opened with the shell: bring it to the front (see AppFocus)
            let name = m["app"] as? String, bundle = m["bundle"] as? String, url = m["url"] as? String
            Task { @MainActor in
                let ok = await AppFocus.bring(name: name, bundle: bundle, url: url)
                self.engine.reply(id, ["ok": ok, "front": NSWorkspace.shared.frontmostApplication?.localizedName ?? ""])
            }
        case "will_act":
            willAct(id, m)
        case "confirm":
            monitor.endActing()
            if model.stopping { engine.reply(id, ["stop": "stopped from the Legato app"]); return }
            let q = m["question"] as? String ?? "do something that can’t be undone"
            ask(id, kind: .engine, question: "The agent wants to \(q).")
        default:
            engine.reply(id, ["error": "unknown request \(method)"])
        }
    }

    private static let clicks: Set<String> = ["click", "double_click", "triple_click", "right_click"]

    private func willAct(_ id: Int, _ m: [String: Any]) {
        if model.stopping || !model.active { engine.reply(id, ["stop": "stopped from the Legato app"]); return }
        if model.phase == .paused { heldWillAct = id; return }
        if pausedSincePlan { replan(id); return }

        let a = m["action"] as? [String: Any] ?? [:]
        let type = a["type"] as? String ?? ""
        let describe = m["describe"] as? String ?? type
        let x = (a["x"] as? NSNumber)?.doubleValue, y = (a["y"] as? NSNumber)?.doubleValue

        var caption = Self.caption(type: type, a: a, describe: describe)
        if let x, let y {
            let p = CGPoint(x: x, y: y)
            hud.avoid(p)
            var el: AX.Element?
            if Self.clicks.contains(type) {
                el = elementUnder(p)
                if let label = el?.label, !label.isEmpty { caption += " · \(label)" }
            }
            model.line = caption
            if settings.ghostCursor { ghost.move(to: p, caption: caption, click: Self.clicks.contains(type)) }
            if settings.guardClicks, Self.clicks.contains(type), let el, let why = AX.risk(of: el.label) {
                let place = el.app.isEmpty ? "" : " in \(el.app)"
                ask(id, kind: .click, question: "\(Self.verb(type)) “\(el.label)”\(place). That \(why).")
                return
            }
        } else {
            model.line = caption
            if settings.ghostCursor { ghost.say(caption) }
        }
        go(id, line: caption)
    }

    private func go(_ id: Int, line: String) {
        model.phase = .acting
        model.line = line
        monitor.beginActing()
        engine.reply(id, ["go": true])
    }

    /// The control under the agent's target. Our own overlay must not answer for it.
    private func elementUnder(_ p: CGPoint) -> AX.Element? {
        guard Permission.accessibility.granted else { return nil }
        let mine = NSRunningApplication.current.localizedName ?? "Legato"
        if let el = AX.element(at: p), el.app != mine { return el }
        return ghost.hidden { AX.element(at: p) }.flatMap { $0.app == mine ? nil : $0 }
    }

    static func verb(_ type: String) -> String {
        switch type {
        case "double_click": return "Double-click"
        case "triple_click": return "Triple-click"
        case "right_click": return "Right-click"
        case "drag": return "Drag"
        case "scroll": return "Scroll"
        default: return "Click"
        }
    }

    static func caption(type: String, a: [String: Any], describe: String) -> String {
        switch type {
        case "click", "double_click", "triple_click", "right_click", "drag": return verb(type)
        case "scroll": return "Scroll \(a["direction"] as? String ?? "down")"
        case "type":
            let t = (a["text"] as? String ?? "").replacingOccurrences(of: "\n", with: "⏎")
            return "Typing “\(t.prefix(40))\(t.count > 40 ? "…" : "")”"
        case "key": return "Pressing " + prettyKeys(a["keys"] as? String ?? "")
        case "wait": return "Waiting"
        default: return describe
        }
    }

    static func prettyKeys(_ keys: String) -> String {
        let map = ["super": "⌘", "cmd": "⌘", "command": "⌘", "ctrl": "⌘", "control": "⌃", "alt": "⌥", "option": "⌥",
                   "shift": "⇧", "Return": "↩", "Escape": "Esc", "Tab": "⇥", "BackSpace": "⌫", "Delete": "⌦",
                   "space": "Space", "Up": "↑", "Down": "↓", "Left": "←", "Right": "→"]
        return keys.split(separator: " ").map { combo in
            combo.split(separator: "+").map { k in map[String(k)] ?? String(k).uppercased() }.joined()
        }.joined(separator: " ")
    }

    // ------------------------------------------------------------ windows
    private func window(_ key: String, title: String, view: some View, resizable: Bool = false) -> NSWindow {
        if let w = windows[key] { return w }
        let host = NSHostingController(rootView: view)
        let w = NSWindow(contentViewController: host)
        w.title = title
        w.styleMask = resizable ? [.titled, .closable, .miniaturizable, .resizable] : [.titled, .closable, .miniaturizable]
        w.isReleasedWhenClosed = false
        w.center()
        windows[key] = w
        return w
    }

    private func present(_ w: NSWindow) {
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }

    func showSettings() {
        let w = window("settings", title: "Legato Settings", view: SettingsView(settings: settings, test: test, runTest: { [weak self] in
            self?.runTest()
        }))
        present(w)
    }

    private func runTest() {
        if !engine.running { try? engine.start() }
        guard engine.running else {
            test.ok = false
            test.result = "The engine isn’t running: set Engine Python under Advanced."
            return
        }
        test.running = true
        test.result = nil
        engine.send(["type": "test", "model": settings.model, "base_url": settings.baseURL, "api_key": settings.apiKey])
    }

    func showHistory(select: String?) {
        history.reload(select: select)
        let w = window("history", title: "Legato History", view: HistoryView(state: history, rerun: { [weak self] task in
            self?.windows["history"]?.orderOut(nil)
            self?.startRun(task: task, context: AX.Context())
        }), resizable: true)
        present(w)
    }

    func showOnboarding() {
        perms.startPolling()
        let w = window("onboarding", title: "Welcome to Legato", view: OnboardingView(
            perms: perms, settings: settings,
            openSettings: { [weak self] in self?.showSettings() },
            relaunch: { [weak self] in self?.relaunch() },
            done: { [weak self] in
                self?.settings.onboarded = true
                self?.perms.stopPolling()
                self?.windows["onboarding"]?.orderOut(nil)
                self?.refreshMenu()
            }))
        present(w)
    }

    private func relaunch() {
        let path = Bundle.main.bundlePath
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/sh")
        p.arguments = ["-c", "sleep 1; open \"\(path)\""]
        try? p.run()
        NSApp.terminate(nil)
    }
}

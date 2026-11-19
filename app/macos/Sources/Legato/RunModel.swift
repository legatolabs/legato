import Foundation

/// What the HUD, the command bar and the menu show about the current run.
final class RunModel: ObservableObject {
    enum Phase: Equatable { case idle, starting, thinking, acting, paused, confirming, finished }

    @Published var phase: Phase = .idle
    @Published var task = ""
    @Published var turn = 0
    @Published var line = ""               // what the agent is doing now
    @Published var thought = ""            // the model's latest words, when it writes any
    @Published var started: Date?
    @Published var cost: Double = 0
    @Published var pausedWhy = ""
    @Published var question = ""           // what the agent wants a yes for
    @Published var status = ""             // done, failed, stopped, error, max_steps
    @Published var message = ""            // the model's final words, or the error
    @Published var wall: Double = 0
    @Published var runID = ""
    @Published var stopping = false
    @Published var feed: [String] = []         // what it has done this run, oldest first (the HUD shows the last few)
    @Published var routine = ""                // "Repeating steps that worked on 18 Nov" when the engine found some

    var active: Bool { [.starting, .thinking, .acting, .paused, .confirming].contains(phase) }

    func reset(task: String, id: String) {
        phase = .starting
        self.task = task
        runID = id
        turn = 0
        line = "Starting…"
        thought = ""
        started = Date()
        cost = 0
        pausedWhy = ""
        question = ""
        status = ""
        message = ""
        wall = 0
        stopping = false
        feed = []
        routine = ""
    }

    /// Add a done step to the feed, in the past tense ("Typing “x”" -> "Typed “x”"), skipping repeats and waits.
    func did(_ caption: String) {
        let past = RunModel.past(caption)
        guard !past.isEmpty, feed.last != past else { return }
        feed.append(past)
        if feed.count > 40 { feed.removeFirst(feed.count - 40) }
    }

    static func past(_ c: String) -> String {
        let swaps = [("Typing ", "Typed "), ("Pressing ", "Pressed "), ("Double-click", "Double-clicked"),
                     ("Triple-click", "Triple-clicked"), ("Right-click", "Right-clicked"), ("Click", "Clicked"),
                     ("Scroll ", "Scrolled "), ("Drag", "Dragged")]
        if c.hasPrefix("Waiting") || c.hasPrefix("Starting") || c.hasPrefix("Thinking") || c.hasPrefix("Looking") { return "" }
        for (a, b) in swaps where c.hasPrefix(a) { return b + c.dropFirst(a.count) }
        return c
    }

    /// The shell and AppleScript steps of an engine "step" event, as feed lines.
    static func toolLines(_ actions: [String]) -> [String] {
        actions.compactMap { a in
            if a.hasPrefix("bash(") {
                let cmd = (a.range(of: "\"command\": \"").map { String(a[$0.upperBound...]) } ?? a)
                    .replacingOccurrences(of: "\\\"", with: "\"")
                let c = cmd.hasSuffix("\"})") ? String(cmd.dropLast(3)) : cmd
                return "Ran " + (c.count > 48 ? String(c.prefix(47)) + "…" : c)
            }
            if a.hasPrefix("applescript(") { return "Ran AppleScript" }
            return nil
        }
    }

    var headline: String {
        switch status {
        case "done": return "Done"
        case "failed": return "The agent gave up"
        case "stopped": return "Stopped"
        case "max_steps": return "Out of turns"
        case "error": return "Something went wrong"
        default: return status.capitalized
        }
    }
}

/// The result of "Test connection" in Settings.
final class TestState: ObservableObject {
    @Published var running = false
    @Published var result: String?
    @Published var ok = false
}

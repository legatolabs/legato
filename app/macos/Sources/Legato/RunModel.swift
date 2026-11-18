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

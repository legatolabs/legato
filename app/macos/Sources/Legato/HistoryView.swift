import AppKit
import SwiftUI

/// One past run, read from its run.json (written by the engine as the run goes).
struct RunRecord: Identifiable, Hashable {
    let id: String
    let dir: URL
    let task: String
    let status: String
    let model: String
    let message: String
    let error: String
    let started: Date
    let wall: Double
    let cost: Double
    let first: String?
    let turns: [Turn]

    struct Turn: Hashable {
        let n: Int
        let text: String
        let actions: [String]
        let shot: String?
        let modelS: Double
    }

    static func load(_ dir: URL) -> RunRecord? {
        guard let data = try? Data(contentsOf: dir.appendingPathComponent("run.json")),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let turns = (j["turns"] as? [[String: Any]] ?? []).map { t in
            Turn(n: t["turn"] as? Int ?? 0, text: t["text"] as? String ?? "", actions: t["actions"] as? [String] ?? [],
                 shot: t["shot"] as? String, modelS: (t["timing"] as? [String: Any])?["model_s"] as? Double ?? 0)
        }
        let usage = j["usage"] as? [String: Any] ?? [:]
        return RunRecord(id: j["id"] as? String ?? dir.lastPathComponent, dir: dir, task: j["task"] as? String ?? "",
                         status: j["status"] as? String ?? "", model: j["model"] as? String ?? "",
                         message: j["message"] as? String ?? "", error: j["error"] as? String ?? "",
                         started: Date(timeIntervalSince1970: j["started"] as? Double ?? 0),
                         wall: j["wall_s"] as? Double ?? 0, cost: usage["cost_usd"] as? Double ?? 0,
                         first: j["first"] as? String, turns: turns)
    }

    static func all(in base: URL) -> [RunRecord] {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: base, includingPropertiesForKeys: nil)) ?? []
        return dirs.compactMap(load).sorted { $0.started > $1.started }
    }
}

final class HistoryState: ObservableObject {
    @Published var runs: [RunRecord] = []
    @Published var selected: String?
    let base: URL

    init(base: URL) { self.base = base }

    func reload(select: String? = nil) {
        runs = RunRecord.all(in: base)
        if let select { selected = select } else if selected == nil { selected = runs.first?.id }
    }
}

struct HistoryView: View {
    @ObservedObject var state: HistoryState
    let rerun: (String) -> Void

    var body: some View {
        NavigationSplitView {
            List(state.runs, selection: $state.selected) { r in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 6) {
                        StatusDot(status: r.status)
                        Text(r.task).lineLimit(1).font(.system(size: 12, weight: .medium))
                    }
                    Text(meta(r)).font(.system(size: 10)).foregroundColor(.secondary).lineLimit(1)
                }
                .padding(.vertical, 2)
                .tag(r.id)
            }
            .navigationSplitViewColumnWidth(min: 220, ideal: 260)
            .overlay {
                if state.runs.isEmpty {
                    Text("No runs yet.\nTap Fn to give Legato a task.").multilineTextAlignment(.center)
                        .foregroundColor(.secondary).font(.system(size: 12))
                }
            }
        } detail: {
            if let r = state.runs.first(where: { $0.id == state.selected }) {
                RunDetail(run: r, rerun: rerun)
            } else {
                Text("Choose a run").foregroundColor(.secondary)
            }
        }
        .frame(minWidth: 760, minHeight: 480)
        .onAppear { state.reload() }
    }

    private func meta(_ r: RunRecord) -> String {
        var parts = [r.started.formatted(date: .abbreviated, time: .shortened)]
        if r.wall > 0 { parts.append(formatDuration(r.wall)) }
        parts.append("\(r.turns.count) turns")
        let c = formatCost(r.cost)
        if !c.isEmpty { parts.append(c) }
        return parts.joined(separator: " · ")
    }
}

struct StatusDot: View {
    let status: String
    var body: some View {
        Circle().fill(color).frame(width: 7, height: 7)
    }
    private var color: Color {
        switch status {
        case "done": return .green
        case "running": return .blue
        case "stopped": return .gray
        default: return .orange
        }
    }
}

struct RunDetail: View {
    let run: RunRecord
    let rerun: (String) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(run.task).font(.system(size: 17, weight: .semibold)).textSelection(.enabled)
                    HStack(spacing: 8) {
                        StatusDot(status: run.status)
                        Text(label).font(.system(size: 12, weight: .medium))
                        Text(stats).font(.system(size: 12)).foregroundColor(.secondary)
                        Spacer()
                        Button("Run again") { rerun(run.task) }
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([run.dir]) }
                    }
                    if !run.message.isEmpty {
                        Text(run.message).font(.system(size: 12)).textSelection(.enabled)
                    }
                    if !run.error.isEmpty {
                        Text(run.error).font(.system(size: 12)).foregroundColor(.red).textSelection(.enabled)
                    }
                }
                Divider()
                if let first = run.first {
                    TurnRow(n: 0, text: "The screen when the task started", actions: [], image: run.dir.appendingPathComponent(first), modelS: 0)
                }
                ForEach(run.turns, id: \.n) { t in
                    TurnRow(n: t.n, text: t.text, actions: t.actions, image: t.shot.map { run.dir.appendingPathComponent($0) },
                            modelS: t.modelS)
                }
            }
            .padding(20)
        }
    }

    private var label: String {
        ["done": "Done", "failed": "Gave up", "stopped": "Stopped", "max_steps": "Out of turns", "error": "Error",
         "running": "Running"][run.status] ?? run.status
    }

    private var stats: String {
        var parts = [run.model]
        if run.wall > 0 { parts.append(formatDuration(run.wall)) }
        parts.append("\(run.turns.count) turns")
        let c = formatCost(run.cost)
        if !c.isEmpty { parts.append(c) }
        return parts.joined(separator: " · ")
    }
}

struct TurnRow: View {
    let n: Int
    let text: String
    let actions: [String]
    let image: URL?
    let modelS: Double

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Group {
                if let image, let ns = NSImage(contentsOf: image) {
                    Image(nsImage: ns).resizable().aspectRatio(contentMode: .fit)
                        .onTapGesture { NSWorkspace.shared.open(image) }
                } else {
                    Rectangle().fill(Color.secondary.opacity(0.1))
                }
            }
            .frame(width: 260, height: 165)
            .clipShape(RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.primary.opacity(0.08)))
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text(n == 0 ? "Start" : "Turn \(n)").font(.system(size: 12, weight: .semibold))
                    if modelS > 0 {
                        Text(String(format: "model %.1f s", modelS)).font(.system(size: 11)).foregroundColor(.secondary)
                    }
                }
                ForEach(Array(actions.enumerated()), id: \.offset) { _, a in
                    Text("• " + a).font(.system(size: 12, design: .monospaced)).textSelection(.enabled)
                }
                if !text.isEmpty {
                    Text(text).font(.system(size: 12)).foregroundColor(.secondary).textSelection(.enabled)
                } else if n > 0 && actions.isEmpty {
                    Text("(no action)").font(.system(size: 12)).foregroundColor(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

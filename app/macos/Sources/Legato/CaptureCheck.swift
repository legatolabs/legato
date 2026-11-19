import AppKit

/// `open -n -g Legato.app --args --capture-check OUT.json` watches the screen for 2 s without touching anything, and
/// writes how often it changes (exact digests, as the engine's wait sees it), how big the changes are, and how long
/// captures take.
/// It runs as Legato.app, so it has Legato's Screen Recording permission.
enum CaptureCheck {
    static func run(out: URL) async {
        var report: [String: Any] = ["front_app": NSWorkspace.shared.frontmostApplication?.localizedName ?? ""]
        let cap = ScreenCapturer()
        let size = await MainActor.run { MainDisplay.frame.size }
        report["screen"] = [size.width, size.height]
        do {
            // the old wait: exact digests of full-size frames
            var exact: [String] = [], fullMs: [Double] = [], cellMs: [Double] = []
            var prevCells: [String] = [], moving = Set<Int>()
            let t0 = Date()
            while Date().timeIntervalSince(t0) < 2 {
                let t = Date()
                let img = try await cap.image(size: size)
                exact.append(ScreenCapturer.md5(img))
                fullMs.append(Date().timeIntervalSince(t) * 1000)
                let tc = Date()
                let cells = ScreenCapturer.cellDigests(img, cell: ScreenCapturer.cell)
                cellMs.append(Date().timeIntervalSince(tc) * 1000)
                if prevCells.count == cells.count {
                    for i in cells.indices where cells[i] != prevCells[i] { moving.insert(i) }
                }
                prevCells = cells
                try await Task.sleep(nanoseconds: 30_000_000)
            }
            // how big the changes are: quarter-size grey frames
            var smallMs: [Double] = [], fractions: [Double] = []
            var prev: [UInt8] = []
            let t1 = Date()
            while Date().timeIntervalSince(t1) < 2 {
                let t = Date()
                let g = try await cap.gray(size: CGSize(width: size.width / 4, height: size.height / 4))
                smallMs.append(Date().timeIntervalSince(t) * 1000)
                if !prev.isEmpty { fractions.append(ScreenCapturer.changed(prev, g)) }
                prev = g
                try await Task.sleep(nanoseconds: 30_000_000)
            }
            func changes(_ xs: [String]) -> Int { zip(xs, xs.dropFirst()).filter { $0 != $1 }.count }
            func median(_ xs: [Double]) -> Double { xs.isEmpty ? 0 : xs.sorted()[xs.count / 2] }
            report["exact"] = ["frames": exact.count, "changes": changes(exact), "median_ms": median(fullMs)]
            // what the wait would leave out: the cells that moved while nothing was touched
            let cols = (Int(size.width) + ScreenCapturer.cell - 1) / ScreenCapturer.cell
            report["cells"] = ["total": prevCells.count, "moving": moving.count, "hash_ms": median(cellMs),
                               "moving_xy": moving.sorted().map { [($0 % cols) * ScreenCapturer.cell, ($0 / cols) * ScreenCapturer.cell] }]
            report["quarter_grey"] = ["frames": smallMs.count, "median_ms": median(smallMs)]
            report["changed_fraction"] = ["median": median(fractions), "max": fractions.max() ?? 0,
                                          "frames_over_threshold": fractions.filter { $0 >= ScreenCapturer.quietFraction }.count,
                                          "frames_with_any_change": fractions.filter { $0 > 0 }.count]
        } catch {
            report["error"] = error.localizedDescription
        }
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: out)
        }
    }
}

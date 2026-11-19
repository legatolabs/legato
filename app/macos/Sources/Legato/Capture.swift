import AppKit
import CryptoKit
import ScreenCaptureKit
import UniformTypeIdentifiers

/// Screenshots of the main display without any of Legato's own windows (HUD, ghost cursor, command bar), so the model
/// never sees or clicks them. ScreenCaptureKit excludes the app by its process; the windows also opt out of capture.
actor ScreenCapturer {
    private var content: SCShareableContent?
    private var contentAt = Date.distantPast

    func invalidate() { content = nil }

    private func shareable(fresh: Bool = false) async throws -> SCShareableContent {
        if !fresh, let content, Date().timeIntervalSince(contentAt) < 3 { return content }
        let c = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
        (content, contentAt) = (c, Date())
        return c
    }

    private func display(_ c: SCShareableContent) throws -> SCDisplay {
        let main = CGMainDisplayID()
        guard let d = c.displays.first(where: { $0.displayID == main }) ?? c.displays.first else {
            throw NSError(domain: "Legato", code: 2, userInfo: [NSLocalizedDescriptionKey: "no display to capture"])
        }
        return d
    }

    /// The whole main display, without Legato's own windows: what the model sees.
    private func screenFilter() async throws -> SCContentFilter {
        let c = try await shareable()
        let me = c.applications.filter { $0.processID == ProcessInfo.processInfo.processIdentifier }
        return SCContentFilter(display: try display(c), excludingApplications: me, exceptingWindows: [])
    }

    /// Only the windows of the app in front (`pid`), where they are on the display: what the wait watches, so another
    /// app animating elsewhere doesn't hold every action to the wait's cap (legato.local's wait_scope="app").
    private func appFilter(pid: pid_t) async throws -> SCContentFilter? {
        var c = try await shareable()
        if !c.applications.contains(where: { $0.processID == pid }) { c = try await shareable(fresh: true) }
        guard let app = c.applications.first(where: { $0.processID == pid }) else { return nil }
        return SCContentFilter(display: try display(c), including: [app], exceptingWindows: [])
    }

    /// The main display at `size` (the engine's logical size, in points), without the cursor.
    func image(size: CGSize, frontPid: pid_t? = nil) async throws -> CGImage {
        let cfg = SCStreamConfiguration()
        cfg.width = Int(size.width)
        cfg.height = Int(size.height)
        cfg.showsCursor = false
        cfg.pixelFormat = kCVPixelFormatType_32BGRA
        cfg.colorSpaceName = CGColorSpace.sRGB
        var filter: SCContentFilter? = nil
        if let pid = frontPid, pid != ProcessInfo.processInfo.processIdentifier { filter = try await appFilter(pid: pid) }
        if filter == nil { filter = try await screenFilter() }
        return try await SCScreenshotManager.captureImage(contentFilter: filter!, configuration: cfg)
    }

    // ---------------------------------------------------------------- waiting for the screen
    // Legato's exact wait: the engine waits until consecutive digests of the full frame match. A wait that ignores
    // caret- or spinner-sized changes was measured and rejected (x0.844 time, -6.8 points on 245 held-out OSWorld
    // tasks, legato-research KNOWLEDGE.md), so the digest stays exact. The tolerance helpers below are only for
    // `--capture-check`, which measures how often a real desktop changes.
    static let quietFraction = 0.002
    static let pixelDelta: Int = 12

    /// `frontPid`: hash only that app's windows (the wait's scope "app"); nil hashes the whole screen.
    func digest(size: CGSize, frontPid: pid_t? = nil) async throws -> String {
        Self.md5(try await image(size: size, frontPid: frontPid))
    }

    /// The display scaled down by ScreenCaptureKit, as one grey byte per pixel.
    func gray(size: CGSize) async throws -> [UInt8] {
        let img = try await image(size: size)
        return Self.grayBytes(img)
    }

    static func grayBytes(_ img: CGImage) -> [UInt8] {
        let w = img.width, h = img.height
        var out = [UInt8](repeating: 0, count: w * h)
        out.withUnsafeMutableBytes { buf in
            guard let ctx = CGContext(data: buf.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)
            else { return }
            ctx.interpolationQuality = .none
            ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
        }
        return out
    }

    /// The fraction of pixels that differ by more than `pixelDelta`.
    static func changed(_ a: [UInt8], _ b: [UInt8]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return 1 }
        var n = 0
        a.withUnsafeBufferPointer { pa in
            b.withUnsafeBufferPointer { pb in
                for i in 0..<pa.count where abs(Int(pa[i]) - Int(pb[i])) > pixelDelta { n += 1 }
            }
        }
        return Double(n) / Double(a.count)
    }

    /// The side of a cell in the grid the wait compares, in points.
    static let cell = 32

    /// A digest for each `cell`-point square of the frame, row by row. The engine's wait compares them cell by cell,
    /// and leaves out the cells that move on their own (a spinner, a progress bar). Exact: any changed pixel changes its
    /// cell's digest (each step of the hash is a bijection of the 8 bytes it takes in).
    static func cellDigests(_ img: CGImage, cell: Int) -> [String] {
        guard let data = img.dataProvider?.data as Data?, cell > 0 else { return [] }
        let w = img.width, h = img.height, bpr = img.bytesPerRow, bpp = max(1, img.bitsPerPixel / 8)
        let cols = (w + cell - 1) / cell, rows = (h + cell - 1) / cell
        var hashes = [UInt64](repeating: 0xcbf2_9ce4_8422_2325, count: cols * rows)
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for y in 0..<h {
                let base = y * bpr, row = (y / cell) * cols
                for c in 0..<cols {
                    var i = base + c * cell * bpp
                    let end = base + min(w, (c + 1) * cell) * bpp
                    var hsh = hashes[row + c]
                    while i + 8 <= end {
                        hsh = (hsh ^ raw.loadUnaligned(fromByteOffset: i, as: UInt64.self)) &* 0x0000_0100_0000_01b3
                        i += 8
                    }
                    while i < end {
                        hsh = (hsh ^ UInt64(raw[i])) &* 0x0000_0100_0000_01b3
                        i += 1
                    }
                    hashes[row + c] = hsh
                }
            }
        }
        return hashes.map { String($0, radix: 36) }
    }

    /// Exact digest of a frame (for the capture check).
    static func md5(_ img: CGImage) -> String {
        guard let data = img.dataProvider?.data as Data? else { return UUID().uuidString }
        return Insecure.MD5.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// A PNG in a temporary file (cheaper than sending megabytes down the pipe). The engine deletes it.
    func png(size: CGSize) async throws -> (URL, CGSize) {
        let img = try await image(size: size)
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("legato-shots", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent(UUID().uuidString + ".png")
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
            throw NSError(domain: "Legato", code: 3, userInfo: [NSLocalizedDescriptionKey: "cannot write screenshot"])
        }
        CGImageDestinationAddImage(dest, img, nil)
        guard CGImageDestinationFinalize(dest) else {
            throw NSError(domain: "Legato", code: 3, userInfo: [NSLocalizedDescriptionKey: "cannot write screenshot"])
        }
        return (url, CGSize(width: img.width, height: img.height))
    }
}

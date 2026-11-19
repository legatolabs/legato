import AppKit
import SwiftUI

/// The brand: ink and white, with neon only on ink (never neon on white).
enum Theme {
    static let ink = Color(red: 0x0A / 255, green: 0x0A / 255, blue: 0x0A / 255)
    static let neon = Color(red: 0xD7 / 255, green: 0xFF / 255, blue: 0x3A / 255)
    static let inkNS = NSColor(srgbRed: 0x0A / 255, green: 0x0A / 255, blue: 0x0A / 255, alpha: 1)
    static let neonNS = NSColor(srgbRed: 0xD7 / 255, green: 0xFF / 255, blue: 0x3A / 255, alpha: 1)
    static let panelFill = Color(red: 0x14 / 255, green: 0x14 / 255, blue: 0x14 / 255).opacity(0.96)
    static let line = Color.white.opacity(0.10)
    static let dim = Color.white.opacity(0.55)
}

/// The whole-note mark, drawn as a template image for the menu bar.
enum MenuBarIcon {
    enum State { case idle, running, paused, attention }

    static func image(_ state: State) -> NSImage {
        let size = NSSize(width: 20, height: 16)
        let img = NSImage(size: size, flipped: false) { rect in
            NSColor.black.setStroke()
            NSColor.black.setFill()
            // the note: an oval tilted like a whole note, with a tilted hole
            let center = NSPoint(x: 8.5, y: rect.midY)
            let outer = NSBezierPath(ovalIn: NSRect(x: center.x - 6.5, y: center.y - 4.6, width: 13, height: 9.2))
            let inner = NSBezierPath(ovalIn: NSRect(x: center.x - 2.4, y: center.y - 3.6, width: 4.8, height: 7.2))
            let tilt = AffineTransform(rotationByDegrees: -24)
            var t = AffineTransform(translationByX: -center.x, byY: -center.y)
            t.append(tilt)
            t.append(AffineTransform(translationByX: center.x, byY: center.y))
            let innerTilt = AffineTransform(rotationByDegrees: 30)
            var ti = AffineTransform(translationByX: -center.x, byY: -center.y)
            ti.append(innerTilt)
            ti.append(AffineTransform(translationByX: center.x, byY: center.y))
            outer.transform(using: t)
            inner.transform(using: ti)
            let note = NSBezierPath()
            note.append(outer)
            note.append(inner.reversed)
            note.windingRule = .evenOdd
            note.fill()
            switch state {
            case .idle:
                break
            case .running:
                NSBezierPath(ovalIn: NSRect(x: 16, y: rect.midY - 2, width: 4, height: 4)).fill()
            case .paused:
                NSBezierPath(rect: NSRect(x: 16, y: rect.midY - 3.5, width: 1.4, height: 7)).fill()
                NSBezierPath(rect: NSRect(x: 18.4, y: rect.midY - 3.5, width: 1.4, height: 7)).fill()
            case .attention:
                NSBezierPath(rect: NSRect(x: 17.3, y: rect.midY - 1, width: 1.6, height: 5.5)).fill()
                NSBezierPath(ovalIn: NSRect(x: 17.3, y: rect.midY - 4, width: 1.6, height: 1.6)).fill()
            }
            return true
        }
        img.isTemplate = true
        return img
    }
}

/// A hosting view that takes the first click even when its panel is not key (buttons in the HUD).
final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// A borderless panel that can still take keyboard focus without activating Legato (the command bar).
final class KeyablePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// The main display: the one with the menu bar, where screen points start at the top left (the engine's coordinates).
enum MainDisplay {
    static var screen: NSScreen { NSScreen.screens.first ?? NSScreen.main! }
    static var frame: NSRect { screen.frame }

    /// Engine coordinates (origin top left, y down) to AppKit coordinates (origin bottom left, y up).
    static func toAppKit(_ p: CGPoint) -> NSPoint {
        NSPoint(x: p.x, y: frame.maxY - p.y)
    }

    static func fromAppKit(_ p: NSPoint) -> CGPoint {
        CGPoint(x: p.x, y: frame.maxY - p.y)
    }

    /// An AppKit rect to engine (top-left) coordinates.
    static func rectFromAppKit(_ r: NSRect) -> CGRect {
        CGRect(x: r.minX, y: frame.maxY - r.maxY, width: r.width, height: r.height)
    }
}

func formatDuration(_ s: Double) -> String {
    let t = Int(s.rounded())
    return t >= 60 ? String(format: "%d:%02d", t / 60, t % 60) : "\(t) s"
}

/// A task's time to the tenth under a minute ("8.2 s"), else minutes and seconds.
func formatSpeed(_ s: Double) -> String {
    s < 60 ? String(format: "%.1f s", s) : formatDuration(s)
}

func formatCost(_ usd: Double) -> String {
    usd <= 0 ? "" : usd < 0.01 ? String(format: "$%.3f", usd) : String(format: "$%.2f", usd)
}

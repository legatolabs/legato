import AppKit
import QuartzCore

/// The ghost cursor: a neon pointer on a click-through layer over the whole main display, showing where the agent
/// acts and what it is about to do ("Click · Send"). It glides there instead of teleporting, and ripples on a click.
/// The layer ignores the mouse and is left out of every screenshot.
final class GhostCursor {
    private let window: NSWindow
    private let root = CALayer()
    private let pointer = CAShapeLayer()
    private let label = CATextLayer()
    private let labelBack = CALayer()
    private var position: CGPoint?
    private var hideWork: DispatchWorkItem?

    init() {
        let frame = MainDisplay.frame
        window = NSWindow(contentRect: frame, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = false
        window.ignoresMouseEvents = true
        window.level = NSWindow.Level(rawValue: Int(CGWindowLevelForKey(.overlayWindow)))
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        window.sharingType = .none
        window.isReleasedWhenClosed = false
        let view = NSView(frame: NSRect(origin: .zero, size: frame.size))
        view.wantsLayer = true
        view.layer = root
        root.frame = view.bounds
        window.contentView = view

        // an arrow pointer, 26 pt tall, tip at the layer's origin
        let path = CGMutablePath()
        let pts: [CGPoint] = [CGPoint(x: 0, y: 0), CGPoint(x: 0, y: -22), CGPoint(x: 6, y: -16.5), CGPoint(x: 10.5, y: -26),
                              CGPoint(x: 14.2, y: -24.4), CGPoint(x: 9.8, y: -15.2), CGPoint(x: 17, y: -15.2)]
        path.addLines(between: pts)
        path.closeSubpath()
        pointer.path = path
        pointer.fillColor = Theme.neonNS.cgColor
        pointer.strokeColor = Theme.inkNS.cgColor
        pointer.lineWidth = 1.6
        pointer.lineJoin = .round
        pointer.shadowColor = NSColor.black.cgColor
        pointer.shadowOpacity = 0.35
        pointer.shadowRadius = 3
        pointer.shadowOffset = CGSize(width: 0, height: -1)
        pointer.bounds = CGRect(x: 0, y: -26, width: 18, height: 26)
        pointer.anchorPoint = CGPoint(x: 0, y: 1)
        pointer.opacity = 0

        labelBack.backgroundColor = Theme.inkNS.withAlphaComponent(0.92).cgColor
        labelBack.cornerRadius = 7
        labelBack.borderColor = Theme.neonNS.withAlphaComponent(0.6).cgColor
        labelBack.borderWidth = 1
        labelBack.opacity = 0
        label.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        label.fontSize = 12
        label.foregroundColor = NSColor.white.cgColor
        label.contentsScale = MainDisplay.screen.backingScaleFactor
        label.alignmentMode = .left
        label.truncationMode = .end
        labelBack.addSublayer(label)
        root.addSublayer(labelBack)
        root.addSublayer(pointer)
    }

    /// Run `body` with the overlay off screen (for an Accessibility lookup under it), then put it back.
    func hidden<T>(_ body: () -> T) -> T {
        let visible = window.isVisible
        if visible { window.orderOut(nil) }
        defer { if visible { window.orderFrontRegardless() } }
        return body()
    }

    func show() {
        hideWork?.cancel()
        window.setFrame(MainDisplay.frame, display: false)
        root.frame = CGRect(origin: .zero, size: MainDisplay.frame.size)
        window.orderFrontRegardless()
    }

    /// Fade out a moment after the run ends.
    func hide(after delay: TimeInterval = 0.8) {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            CATransaction.begin()
            CATransaction.setAnimationDuration(0.3)
            self.pointer.opacity = 0
            self.labelBack.opacity = 0
            CATransaction.commit()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { self.window.orderOut(nil); self.position = nil }
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    /// Glide to `p` (engine coordinates) with a caption. A click ripples once the pointer arrives.
    func move(to p: CGPoint, caption: String, click: Bool) {
        show()
        let target = CGPoint(x: p.x, y: MainDisplay.frame.height - p.y)     // layer coordinates: y up
        let from = position.map { CGPoint(x: $0.x, y: MainDisplay.frame.height - $0.y) } ?? target
        position = p
        let distance = hypot(target.x - from.x, target.y - from.y)
        let duration = min(0.28, max(0.12, Double(distance) / 2600))

        CATransaction.begin()
        CATransaction.setAnimationDuration(duration)
        CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(name: .easeOut))
        pointer.position = target
        pointer.opacity = 1
        layoutLabel(caption, near: target)
        CATransaction.commit()
        if click {
            DispatchQueue.main.asyncAfter(deadline: .now() + duration) { [weak self] in self?.ripple(at: target) }
        }
    }

    /// A caption where the pointer already is (typing, a key press).
    func say(_ caption: String) {
        guard let p = position else { return }
        let at = CGPoint(x: p.x, y: MainDisplay.frame.height - p.y)
        CATransaction.begin()
        CATransaction.setAnimationDuration(0.12)
        layoutLabel(caption, near: at)
        CATransaction.commit()
    }

    private func layoutLabel(_ caption: String, near p: CGPoint) {
        guard !caption.isEmpty else { labelBack.opacity = 0; return }
        let font = NSFont.systemFont(ofSize: 12, weight: .medium)
        let width = min(320, (caption as NSString).size(withAttributes: [.font: font]).width + 2)
        label.string = caption
        let size = CGSize(width: width + 16, height: 22)
        var origin = CGPoint(x: p.x + 20, y: p.y - 40)
        let bounds = root.bounds
        if origin.x + size.width > bounds.maxX - 8 { origin.x = p.x - size.width - 8 }
        if origin.y < bounds.minY + 8 { origin.y = p.y + 8 }
        labelBack.frame = CGRect(origin: origin, size: size)
        label.frame = CGRect(x: 8, y: 3, width: width, height: 16)
        labelBack.opacity = 1
    }

    private func ripple(at p: CGPoint) {
        let ring = CAShapeLayer()
        ring.path = CGPath(ellipseIn: CGRect(x: -14, y: -14, width: 28, height: 28), transform: nil)
        ring.position = p
        ring.fillColor = Theme.neonNS.withAlphaComponent(0.25).cgColor
        ring.strokeColor = Theme.neonNS.cgColor
        ring.lineWidth = 2
        root.insertSublayer(ring, below: pointer)
        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 0.4
        scale.toValue = 1.6
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        let group = CAAnimationGroup()
        group.animations = [scale, fade]
        group.duration = 0.45
        group.timingFunction = CAMediaTimingFunction(name: .easeOut)
        ring.opacity = 0
        ring.add(group, forKey: "ripple")
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { ring.removeFromSuperlayer() }
    }
}

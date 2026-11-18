import AppKit
import Carbon.HIToolbox

/// One listen-only event tap for everything Legato hears from the real keyboard and mouse:
/// - the person taking over during a run (any click, key, scroll, or a real mouse move);
/// - a tap on Fn (the Globe key) to open the command bar;
/// - Escape, which stops a run.
///
/// The agent's own input must not count as the person's. Two checks: an event posted by the engine's process carries
/// its pid, and while the engine is acting (from the go-ahead to its "acted") nothing counts.
final class InputMonitor {
    var onTakeover: ((String) -> Void)?
    var onEscape: (() -> Void)?
    var onFnTap: (() -> Void)?

    /// Set by the app: whether a run is active, so input counts as taking over.
    var watching = false
    var enginePid: pid_t = 0
    private var actingUntil = Date.distantPast
    private var acting = false

    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var moved: CGFloat = 0
    private var lastMove: CGPoint?
    private var fnDownAt: Date?
    private var fnClean = false

    var installed: Bool { tap != nil }

    func beginActing() { acting = true }

    func endActing() {
        acting = false
        actingUntil = Date().addingTimeInterval(0.25)      // events still in flight after the engine's last post
    }

    @discardableResult
    func install() -> Bool {
        if tap != nil { return true }
        // without Input Monitoring the tap would fail and macOS would prompt on its own; the setup window asks instead
        guard CGPreflightListenEventAccess() else { return false }
        let types: [CGEventType] = [.mouseMoved, .leftMouseDown, .rightMouseDown, .otherMouseDown, .leftMouseDragged,
                                    .scrollWheel, .keyDown, .flagsChanged]
        let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
        let me = Unmanaged.passUnretained(self).toOpaque()
        guard let t = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                                        eventsOfInterest: mask, callback: { _, type, event, ctx in
            let monitor = Unmanaged<InputMonitor>.fromOpaque(ctx!).takeUnretainedValue()
            monitor.handle(type, event)
            return Unmanaged.passUnretained(event)
        }, userInfo: me) else { return false }
        tap = t
        source = CFMachPortCreateRunLoopSource(nil, t, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: t, enable: true)
        return true
    }

    private func isAgents(_ event: CGEvent) -> Bool {
        let pid = pid_t(truncatingIfNeeded: event.getIntegerValueField(.eventSourceUnixProcessID))
        if enginePid != 0 && pid == enginePid { return true }
        if pid == ProcessInfo.processInfo.processIdentifier { return true }
        return acting || Date() < actingUntil
    }

    private func handle(_ type: CGEventType, _ event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return
        }
        // Fn alone, pressed and let go quickly with nothing else in between
        if type == .flagsChanged {
            let code = event.getIntegerValueField(.keyboardEventKeycode)
            if code == Int64(kVK_Function) {
                if event.flags.contains(.maskSecondaryFn) {
                    fnDownAt = Date()
                    fnClean = true
                } else if let t = fnDownAt, fnClean, Date().timeIntervalSince(t) < 0.45, !isAgents(event) {
                    fnDownAt = nil
                    DispatchQueue.main.async { self.onFnTap?() }
                }
            } else {
                fnClean = false
            }
            return
        }
        if type == .keyDown { fnClean = false }
        guard watching, !isAgents(event) else {
            if type == .mouseMoved { lastMove = event.location }
            return
        }
        switch type {
        case .mouseMoved, .leftMouseDragged:
            let p = event.location
            if let last = lastMove { moved += hypot(p.x - last.x, p.y - last.y) }
            lastMove = p
            if moved > 12 { fire("moved the mouse") }
        case .keyDown:
            if event.getIntegerValueField(.keyboardEventKeycode) == Int64(kVK_Escape) {
                DispatchQueue.main.async { self.onEscape?() }
            } else {
                fire("pressed a key")
            }
        case .scrollWheel:
            fire("scrolled")
        default:
            fire("clicked")
        }
    }

    private func fire(_ why: String) {
        moved = 0
        DispatchQueue.main.async { self.onTakeover?(why) }
    }

    func resetMovement() {
        moved = 0
        lastMove = nil
    }
}

/// A global shortcut that needs no permission (Carbon hot key): ⌥⇧Space opens the command bar, for keyboards
/// without Fn or when the Fn tap is off.
final class HotKey {
    private var ref: EventHotKeyRef?
    private var handler: EventHandlerRef?
    var onPress: (() -> Void)?

    func register(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(optionKey | shiftKey)) {
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let me = Unmanaged.passUnretained(self).toOpaque()
        InstallEventHandler(GetApplicationEventTarget(), { _, _, ctx in
            let hk = Unmanaged<HotKey>.fromOpaque(ctx!).takeUnretainedValue()
            DispatchQueue.main.async { hk.onPress?() }
            return noErr
        }, 1, &spec, me, &handler)
        let id = EventHotKeyID(signature: OSType(0x4C47_544F), id: 1)       // "LGTO"
        RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &ref)
    }
}

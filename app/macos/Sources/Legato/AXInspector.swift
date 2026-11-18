import AppKit
import ApplicationServices

/// What is under a point, and where the person is, read through the Accessibility API.
/// Every call has a short timeout: an app that does not answer must not hold up an action.
enum AX {
    struct Element {
        let role: String
        let label: String
        let app: String
    }

    private static func attr(_ el: AXUIElement, _ name: String) -> AnyObject? {
        var v: AnyObject?
        return AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success ? v : nil
    }

    private static func string(_ el: AXUIElement, _ name: String) -> String? {
        (attr(el, name) as? String)?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
    }

    private static let actionable: Set<String> = ["AXButton", "AXMenuItem", "AXMenuBarItem", "AXLink", "AXCheckBox",
                                                  "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXCell"]

    /// The control at `p` (screen points, origin top left): the nearest clickable element and its visible label.
    static func element(at p: CGPoint) -> Element? {
        let sys = AXUIElementCreateSystemWide()
        AXUIElementSetMessagingTimeout(sys, 0.25)
        var hit: AXUIElement?
        guard AXUIElementCopyElementAtPosition(sys, Float(p.x), Float(p.y), &hit) == .success, var el = hit else {
            return nil
        }
        AXUIElementSetMessagingTimeout(el, 0.25)
        var pid: pid_t = 0
        AXUIElementGetPid(el, &pid)
        let app = NSRunningApplication(processIdentifier: pid)?.localizedName ?? ""
        var first: Element?
        for _ in 0..<4 {
            let role = string(el, kAXRoleAttribute) ?? ""
            let label = string(el, kAXTitleAttribute) ?? string(el, kAXDescriptionAttribute)
                ?? (role == "AXStaticText" ? string(el, kAXValueAttribute) : nil) ?? string(el, kAXHelpAttribute) ?? ""
            let e = Element(role: role, label: String(label.prefix(80)), app: app)
            if first == nil && !label.isEmpty { first = e }
            if actionable.contains(role) && !label.isEmpty { return e }
            guard let parent = attr(el, kAXParentAttribute) else { break }
            el = parent as! AXUIElement
        }
        return first
    }

    // Words on a control that mean the click can't be taken back: it sends, pays, deletes or signs out.
    private static let risky: [(String, String)] = [
        ("delete", "deletes something"), ("remove", "removes something"), ("erase", "erases something"),
        ("empty trash", "empties the Trash"), ("move to trash", "moves it to the Trash"), ("discard", "discards changes"),
        ("send", "sends something"), ("pay", "pays"), ("buy", "buys something"), ("purchase", "buys something"),
        ("place order", "places an order"), ("checkout", "starts a payment"), ("check out", "starts a payment"),
        ("transfer", "moves money or data"), ("sign out", "signs you out"), ("log out", "logs you out"),
        ("publish", "publishes something"), ("post", "posts publicly"), ("uninstall", "uninstalls something"),
    ]

    /// Why clicking a control with this label needs the person's yes, or nil.
    static func risk(of label: String) -> String? {
        let l = label.lowercased()
        for (word, why) in risky {
            let pattern = "\\b" + NSRegularExpression.escapedPattern(for: word) + "\\b"
            if l.range(of: pattern, options: .regularExpression) != nil { return why }
        }
        return nil
    }

    struct Context {
        var app: String = ""
        var window: String = ""
        var selection: String = ""
        var appIcon: NSImage?

        var summary: String {
            var parts: [String] = []
            if !app.isEmpty { parts.append(window.isEmpty ? "in \(app)" : "in \(app), window “\(window)”") }
            if !selection.isEmpty { parts.append("with this text selected: “\(selection)”") }
            return parts.joined(separator: ", ")
        }
    }

    /// The app in front, its focused window's title and the selected text, when the command bar opens.
    static func frontContext() -> Context {
        var c = Context()
        guard let front = NSWorkspace.shared.frontmostApplication else { return c }
        c.app = front.localizedName ?? ""
        c.appIcon = front.icon
        let app = AXUIElementCreateApplication(front.processIdentifier)
        AXUIElementSetMessagingTimeout(app, 0.25)
        if let win = attr(app, kAXFocusedWindowAttribute) {
            c.window = string(win as! AXUIElement, kAXTitleAttribute) ?? ""
        }
        if let focused = attr(app, kAXFocusedUIElementAttribute) {
            let sel = string(focused as! AXUIElement, kAXSelectedTextAttribute) ?? ""
            c.selection = String(sel.prefix(2000))
        }
        c.window = String(c.window.prefix(120))
        return c
    }
}

extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

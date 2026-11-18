import Foundation
import Security

/// Where the model is: a preset fills the base URL and suggests models; the key is kept in Keychain per preset.
struct ProviderPreset: Identifiable, Hashable {
    let id: String
    let name: String
    let baseURL: String
    let models: [String]
    let needsKey: Bool
    let keyHint: String

    static let all: [ProviderPreset] = [
        ProviderPreset(id: "openrouter", name: "OpenRouter", baseURL: "https://openrouter.ai/api/v1",
                       models: ["qwen/qwen3.8-27b", "anthropic/claude-sonnet-5.5", "qwen/qwen4-max", "openai/gpt-6.1-terra"],
                       needsKey: true, keyHint: "sk-or-…"),
        ProviderPreset(id: "anthropic", name: "Anthropic", baseURL: "https://api.anthropic.com/v1",
                       models: ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
                       needsKey: true, keyHint: "sk-ant-…"),
        ProviderPreset(id: "local", name: "Local server", baseURL: "http://localhost:8000/v1",
                       models: [], needsKey: false, keyHint: "usually none"),
        ProviderPreset(id: "custom", name: "Other (OpenAI-compatible)", baseURL: "",
                       models: [], needsKey: true, keyHint: "the provider's key"),
    ]

    static func find(_ id: String) -> ProviderPreset { all.first { $0.id == id } ?? all[0] }
}

/// Settings in UserDefaults; the API key in Keychain. Observed by the settings window and read at each run.
final class AppSettings: ObservableObject {
    static let shared = AppSettings()
    private let d = UserDefaults.standard

    @Published var providerID: String { didSet { d.set(providerID, forKey: "providerID") } }
    @Published var baseURL: String { didSet { d.set(baseURL, forKey: "baseURL") } }
    @Published var model: String { didSet { d.set(model, forKey: "model") } }
    @Published var maxSteps: Int { didSet { d.set(maxSteps, forKey: "maxSteps") } }
    /// Screenshots the model keeps in its prompt. 4: 32% less time and 25% less cost a task than 8, and 5 points
    /// lower on held-out Mac tasks (legato-research EXP-0030). 8 is Legato's default.
    @Published var imageMax: Int { didSet { d.set(imageMax, forKey: "imageMax") } }
    @Published var guardClicks: Bool { didSet { d.set(guardClicks, forKey: "guardClicks") } }
    /// Offer the model a shell beside the screen (the engine's `bash` tool, AppleScript included). What can be undone
    /// runs at once; deleting, overwriting, sending, quitting apps, system settings and private data wait for the
    /// person's yes (app_engine.command_risk). On by default: legato-research EXP-0037 measured it 43% faster a task
    /// (118 s -> 67 s on hard-dev) with more passes (87.5% against 66.7%).
    @Published var allowShell: Bool { didSet { d.set(allowShell, forKey: "allowShell") } }
    @Published var ghostCursor: Bool { didSet { d.set(ghostCursor, forKey: "ghostCursor") } }
    @Published var fnKey: Bool { didSet { d.set(fnKey, forKey: "fnKey") } }
    @Published var enginePython: String { didSet { d.set(enginePython, forKey: "enginePython") } }
    @Published var onboarded: Bool { didSet { d.set(onboarded, forKey: "onboarded") } }

    init() {
        d.register(defaults: ["providerID": "openrouter", "baseURL": ProviderPreset.all[0].baseURL,
                              "model": ProviderPreset.all[0].models[0], "maxSteps": 40, "imageMax": 4, "guardClicks": true, "allowShell": true,
                              "ghostCursor": true, "fnKey": true, "enginePython": "", "onboarded": false])
        providerID = d.string(forKey: "providerID") ?? "openrouter"
        baseURL = d.string(forKey: "baseURL") ?? ""
        model = d.string(forKey: "model") ?? ""
        maxSteps = d.integer(forKey: "maxSteps")
        imageMax = d.integer(forKey: "imageMax")
        guardClicks = d.bool(forKey: "guardClicks")
        allowShell = d.bool(forKey: "allowShell")
        ghostCursor = d.bool(forKey: "ghostCursor")
        fnKey = d.bool(forKey: "fnKey")
        enginePython = d.string(forKey: "enginePython") ?? ""
        onboarded = d.bool(forKey: "onboarded")
    }

    var preset: ProviderPreset { ProviderPreset.find(providerID) }

    func choose(_ p: ProviderPreset) {
        providerID = p.id
        if !p.baseURL.isEmpty { baseURL = p.baseURL }
        if let m = p.models.first, !p.models.contains(model) { model = m }
    }

    var apiKey: String {
        get { Keychain.read(account: providerID) ?? "" }
        set {
            objectWillChange.send()
            if newValue.isEmpty { Keychain.delete(account: providerID) } else { Keychain.write(newValue, account: providerID) }
        }
    }

    /// Ready to run: a base URL, a model, and a key unless the server is local.
    var problem: String? {
        if baseURL.trimmingCharacters(in: .whitespaces).isEmpty { return "Set the model's base URL in Settings." }
        if model.trimmingCharacters(in: .whitespaces).isEmpty { return "Choose a model in Settings." }
        let local = ["localhost", "127.0.0.1", "0.0.0.0"].contains { baseURL.contains($0) }
        if !local && apiKey.isEmpty { return "Add your \(preset.name) API key in Settings." }
        return nil
    }

    var runsDir: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("Legato/runs", isDirectory: true)
    }
}

enum Keychain {
    static let service = "computer.legato.mac.api-key"

    static func read(account: String) -> String? {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account, kSecReturnData as String: true,
                                kSecMatchLimit as String: kSecMatchLimitOne]
        var out: AnyObject?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ value: String, account: String) {
        let data = Data(value.utf8)
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        if SecItemUpdate(q as CFDictionary, [kSecValueData as String: data] as CFDictionary) == errSecItemNotFound {
            var add = q
            add[kSecValueData as String] = data
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(add as CFDictionary, nil)
        }
    }

    static func delete(account: String) {
        let q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                kSecAttrAccount as String: account]
        SecItemDelete(q as CFDictionary)
    }
}

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

    /// The model makers first, then the inference providers and routers (OpenRouter, Together, Fireworks, Groq,
    /// Cerebras), then any OpenAI-compatible server (Legato Cloud, a server of your own).
    static let all: [ProviderPreset] = [
        ProviderPreset(id: "openai", name: "OpenAI", baseURL: "https://api.openai.com/v1",
                       models: [], needsKey: true, keyHint: "sk-…"),
        ProviderPreset(id: "anthropic", name: "Claude (Anthropic)", baseURL: "https://api.anthropic.com/v1",
                       models: ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"],
                       needsKey: true, keyHint: "sk-ant-…"),
        ProviderPreset(id: "gemini", name: "Gemini (Google)", baseURL: "https://generativelanguage.googleapis.com/v1beta/openai",
                       models: [], needsKey: true, keyHint: "AIza…"),
        ProviderPreset(id: "xai", name: "Grok (xAI)", baseURL: "https://api.x.ai/v1",
                       models: [], needsKey: true, keyHint: "xai-…"),
        ProviderPreset(id: "openrouter", name: "OpenRouter", baseURL: "https://openrouter.ai/api/v1",
                       models: ["qwen/qwen3.8-27b", "anthropic/claude-sonnet-5.5", "anthropic/claude-haiku-5.5", "qwen/qwen4-max",
                                "openai/gpt-6.1-terra"],
                       needsKey: true, keyHint: "sk-or-…"),
        ProviderPreset(id: "together", name: "Together AI", baseURL: "https://api.together.xyz/v1",
                       models: [], needsKey: true, keyHint: "the Together key"),
        ProviderPreset(id: "fireworks", name: "Fireworks AI", baseURL: "https://api.fireworks.ai/inference/v1",
                       models: [], needsKey: true, keyHint: "fw_…"),
        ProviderPreset(id: "groq", name: "Groq", baseURL: "https://api.groq.com/openai/v1",
                       models: [], needsKey: true, keyHint: "gsk_…"),
        ProviderPreset(id: "cerebras", name: "Cerebras", baseURL: "https://api.cerebras.ai/v1",
                       models: [], needsKey: true, keyHint: "csk-…"),
        ProviderPreset(id: "custom", name: "Custom (OpenAI-compatible)", baseURL: "",
                       models: [], needsKey: false, keyHint: "the server's key, if it has one"),
    ]

    /// Where a new install starts: OpenRouter with the model the Mac champion was measured with.
    static let start = find("openrouter")

    static func find(_ id: String) -> ProviderPreset { all.first { $0.id == id } ?? all.first { $0.id == "openrouter" }! }

    /// A model id shown short: the part after the provider's prefix ("anthropic/claude-sonnet-5.5" -> "claude-sonnet-5.5",
    /// "accounts/fireworks/models/x" -> "x").
    static func short(_ model: String) -> String {
        String(model.split(separator: "/").last ?? Substring(model))
    }
}

/// A finished task and its answer: what a follow-up builds on.
struct PriorResult {
    let task: String
    let result: String
    let at: Date
}

/// One model at one provider: what the command bar switches between.
struct ModelChoice: Codable, Hashable, Identifiable {
    let provider: String
    let model: String
    var id: String { provider + "|" + model }
    var preset: ProviderPreset { ProviderPreset.find(provider) }
    var label: String { ProviderPreset.short(model) }
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
    /// Hold Fn to say the task (on-device speech recognition); let go to run it.
    @Published var holdToTalk: Bool { didSet { d.set(holdToTalk, forKey: "holdToTalk") } }
    /// A task done before is started with the steps that finished it then (app_engine.known_routine).
    @Published var repeats: Bool { didSet { d.set(repeats, forKey: "repeats") } }
    @Published var enginePython: String { didSet { d.set(enginePython, forKey: "enginePython") } }
    @Published var onboarded: Bool { didSet { d.set(onboarded, forKey: "onboarded") } }

    init() {
        d.register(defaults: ["providerID": ProviderPreset.start.id, "baseURL": ProviderPreset.start.baseURL,
                              "model": ProviderPreset.start.models[0], "maxSteps": 40, "imageMax": 4, "guardClicks": true, "allowShell": true,
                              "ghostCursor": true, "fnKey": true, "holdToTalk": true, "repeats": true, "enginePython": "", "onboarded": false])
        providerID = d.string(forKey: "providerID") ?? "openrouter"
        baseURL = d.string(forKey: "baseURL") ?? ""
        model = d.string(forKey: "model") ?? ""
        maxSteps = d.integer(forKey: "maxSteps")
        imageMax = d.integer(forKey: "imageMax")
        guardClicks = d.bool(forKey: "guardClicks")
        allowShell = d.bool(forKey: "allowShell")
        ghostCursor = d.bool(forKey: "ghostCursor")
        fnKey = d.bool(forKey: "fnKey")
        holdToTalk = d.bool(forKey: "holdToTalk")
        repeats = d.bool(forKey: "repeats")
        enginePython = d.string(forKey: "enginePython") ?? ""
        onboarded = d.bool(forKey: "onboarded")
        recents = load("recents", [ModelChoice].self) ?? []
        pastTasks = d.stringArray(forKey: "pastTasks") ?? []
        catalog = load("catalog", [String: [String]].self) ?? [:]
        catalogDate = load("catalogDate", [String: Date].self) ?? [:]
        if recents.isEmpty && !model.isEmpty { recents = [current] }
    }

    var preset: ProviderPreset { ProviderPreset.find(providerID) }

    func choose(_ p: ProviderPreset) {
        if !baseURL.isEmpty { d.set(baseURL, forKey: "baseURL." + providerID) }   // each provider keeps its own URL
        providerID = p.id
        baseURL = d.string(forKey: "baseURL." + p.id) ?? p.baseURL
        if baseURL.isEmpty { baseURL = p.baseURL }
        let known = p.models + (catalog[p.id] ?? [])
        if let m = known.first, !known.contains(model) { model = m }
    }

    /// Switch to `c` (the command bar's chips and picker): its provider, that provider's URL and key, and the model.
    func select(_ c: ModelChoice) {
        if c.provider != providerID { choose(c.preset) }
        model = c.model
        noteUsed()
    }

    var current: ModelChoice { ModelChoice(provider: providerID, model: model) }

    var apiKey: String {
        get { Keychain.read(account: providerID) ?? "" }
        set {
            objectWillChange.send()
            if newValue.isEmpty { Keychain.delete(account: providerID) } else { Keychain.write(newValue, account: providerID) }
        }
    }

    func key(for provider: String) -> String { Keychain.read(account: provider) ?? "" }

    func baseURL(for provider: String) -> String {
        if provider == providerID { return baseURL }
        return d.string(forKey: "baseURL." + provider) ?? ProviderPreset.find(provider).baseURL
    }

    /// Ready to use without a trip to Settings: a URL, and a key unless it needs none.
    func usable(_ provider: String) -> Bool {
        let p = ProviderPreset.find(provider)
        return !baseURL(for: provider).isEmpty && (!p.needsKey || !key(for: provider).isEmpty)
    }

    // ------------------------------------------------------------ recent models, past tasks, fetched model lists
    /// The models used last, newest first (the command bar's chips: ⌘1-⌘4).
    @Published var recents: [ModelChoice] = [] { didSet { save(recents, "recents") } }
    /// Tasks typed before, newest first (↑ in the command bar).
    @Published var pastTasks: [String] = [] { didSet { d.set(pastTasks, forKey: "pastTasks") } }
    /// Each provider's models as its /models listed them (ModelCatalog), with when.
    @Published var catalog: [String: [String]] = [:] { didSet { save(catalog, "catalog") } }
    @Published var catalogDate: [String: Date] = [:] { didSet { save(catalogDate, "catalogDate") } }

    func noteUsed() {
        let c = current
        guard !c.model.isEmpty else { return }
        recents = [c] + recents.filter { $0 != c }.prefix(5)
    }

    func noteTask(_ t: String) {
        pastTasks = [t] + pastTasks.filter { $0 != t }.prefix(49)
    }

    /// Every model the picker can offer: recents first, then each usable provider's suggestions and fetched list.
    var choices: [ModelChoice] {
        var out: [ModelChoice] = recents
        for p in ProviderPreset.all where usable(p.id) {
            for m in p.models + (catalog[p.id] ?? []) {
                let c = ModelChoice(provider: p.id, model: m)
                if !out.contains(c) { out.append(c) }
            }
        }
        if !current.model.isEmpty && !out.contains(current) { out.insert(current, at: 0) }
        return out
    }

    private func save<T: Encodable>(_ v: T, _ key: String) {
        if let data = try? JSONEncoder().encode(v) { d.set(data, forKey: key) }
    }

    private func load<T: Decodable>(_ key: String, _ type: T.Type) -> T? {
        guard let data = d.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    /// Ready to run: a base URL, a model, and a key unless the server is local.
    var problem: String? {
        if baseURL.trimmingCharacters(in: .whitespaces).isEmpty { return "Set the model's base URL in Settings." }
        if model.trimmingCharacters(in: .whitespaces).isEmpty { return "Choose a model in Settings." }
        let local = ["localhost", "127.0.0.1", "0.0.0.0"].contains { baseURL.contains($0) }
        if !local && preset.needsKey && apiKey.isEmpty { return "Add your \(preset.name) API key in Settings." }
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

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

/// The model `legato setup` saved for the command line, the MCP server and Python (legato/config.py):
/// ~/.config/legato/config.json, or $LEGATO_CONFIG, or $XDG_CONFIG_HOME/legato/config.json. The app starts from it
/// until a model is chosen in the app, so a person who ran `legato setup` does not set the model twice.
struct SharedSetup {
    let provider: String        // an id of ProviderPreset.all ("custom" for a local server or a provider the app lacks)
    let model: String
    let baseURL: String
    let apiKey: String          // a key pasted into `legato setup`, if one was ("" otherwise)

    static var path: URL {
        let env = ProcessInfo.processInfo.environment
        if let p = env["LEGATO_CONFIG"], !p.isEmpty { return URL(fileURLWithPath: (p as NSString).expandingTildeInPath) }
        if let x = env["XDG_CONFIG_HOME"], !x.isEmpty { return URL(fileURLWithPath: x).appendingPathComponent("legato/config.json") }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config/legato/config.json")
    }

    static func load(from url: URL = path) -> SharedSetup? {
        guard let data = try? Data(contentsOf: url),
              let j = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let model = (j["model"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        let url = (j["base_url"] as? String ?? "").trimmingCharacters(in: .whitespaces)
        guard !model.isEmpty, !url.isEmpty else { return nil }
        let id = j["provider"] as? String ?? ""
        let known = ProviderPreset.all.contains { $0.id == id && $0.id != "custom" }
        return SharedSetup(provider: known ? id : "custom", model: model, baseURL: url, apiKey: j["api_key"] as? String ?? "")
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
    /// The most one task may cost in USD (0: no limit). Only providers that report a cost (OpenRouter) count.
    @Published var maxCost: Double { didSet { d.set(maxCost, forKey: "maxCost") } }
    /// Screenshots the model keeps in its prompt. 4: 32% less time and 25% less cost a task than 8, and 5 points
    /// lower on held-out Mac tasks. 8 is Legato's default.
    @Published var imageMax: Int { didSet { d.set(imageMax, forKey: "imageMax") } }
    @Published var guardClicks: Bool { didSet { d.set(guardClicks, forKey: "guardClicks") } }
    /// Offer the model a shell beside the screen (the engine's `bash` tool, AppleScript included). What can be undone
    /// runs at once; deleting, overwriting, sending, quitting apps, system settings and private data wait for the
    /// person's yes (app_engine.command_risk). On by default: we measured it 43% faster a task
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

    /// What `legato setup` saved, when the app has no model of its own yet (nothing chosen in the app).
    private var shared: SharedSetup?

    init() {
        let own = d.object(forKey: "model") != nil || d.object(forKey: "providerID") != nil
        d.register(defaults: ["providerID": ProviderPreset.start.id, "baseURL": ProviderPreset.start.baseURL,
                              "model": ProviderPreset.start.models[0], "maxSteps": 40, "maxCost": 1.0, "imageMax": 4, "guardClicks": true, "allowShell": true,
                              "ghostCursor": true, "fnKey": true, "holdToTalk": true, "repeats": true, "enginePython": "", "onboarded": false])
        providerID = d.string(forKey: "providerID") ?? "openrouter"
        baseURL = d.string(forKey: "baseURL") ?? ""
        model = d.string(forKey: "model") ?? ""
        if !own, let s = SharedSetup.load() {       // not saved here: the app follows the shared setup until it has its own
            shared = s
            providerID = s.provider
            baseURL = s.baseURL
            model = s.model
        }
        maxSteps = d.integer(forKey: "maxSteps")
        maxCost = d.double(forKey: "maxCost")
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

    /// Switch provider. Each provider keeps its own URL and the model last used with it; a provider with neither a
    /// remembered nor a known model gets none, so `problem` asks for one instead of sending another provider's model id.
    func choose(_ p: ProviderPreset) {
        if !baseURL.isEmpty { d.set(baseURL, forKey: "baseURL." + providerID) }
        if !model.isEmpty { d.set(model, forKey: "model." + providerID) }
        providerID = p.id
        baseURL = d.string(forKey: "baseURL." + p.id) ?? p.baseURL
        if baseURL.isEmpty { baseURL = p.baseURL }
        let known = p.models + (catalog[p.id] ?? [])
        if let m = d.string(forKey: "model." + p.id), !m.isEmpty {
            model = m
        } else if !known.contains(model) {
            model = known.first ?? ""
        }
    }

    /// Switch to `c` (the command bar's chips and picker): its provider, that provider's URL and key, and the model.
    func select(_ c: ModelChoice) {
        if c.provider != providerID { choose(c.preset) }
        model = c.model
        noteUsed()
    }

    var current: ModelChoice { ModelChoice(provider: providerID, model: model) }

    var apiKey: String {
        get { key(for: providerID) }
        set {
            objectWillChange.send()
            if newValue.isEmpty { Keychain.delete(account: providerID) } else { Keychain.write(newValue, account: providerID) }
        }
    }

    /// The provider's key from the Keychain; without one, the key `legato setup` saved for that provider.
    func key(for provider: String) -> String {
        if let k = Keychain.read(account: provider), !k.isEmpty { return k }
        if let s = shared, s.provider == provider { return s.apiKey }
        return ""
    }

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

/// Every provider's key in one Keychain item (a JSON object, provider id -> key), read once and kept in memory.
/// macOS asks to unlock each item an app reads, so one item means at most one prompt, and none after Always Allow.
/// Keys from before (one item per provider) are moved into it the first time it is read, then deleted.
enum Keychain {
    /// "computer.legato.mac.api-key" for the release; a build with another bundle id (a test copy) gets its own.
    static let service = (Bundle.main.bundleIdentifier ?? "computer.legato.mac") + ".api-key"
    static let account = "keys"

    private static let lock = NSLock()
    private static var cache: [String: String]?

    static func read(account provider: String) -> String? {
        lock.lock(); defer { lock.unlock() }
        return loaded()[provider]
    }

    static func write(_ value: String, account provider: String) {
        lock.lock(); defer { lock.unlock() }
        var keys = loaded()
        guard keys[provider] != value else { return }
        keys[provider] = value
        if save(keys) { cache = keys }
    }

    static func delete(account provider: String) {
        lock.lock(); defer { lock.unlock() }
        var keys = loaded()
        guard keys.removeValue(forKey: provider) != nil else { return }
        if save(keys) { cache = keys }
    }

    // ------------------------------------------------------------ the one item
    private static func query(_ account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    /// The keys, from memory or the Keychain. A read the person denied gives no keys this launch and changes nothing
    /// in the Keychain; only an item that is not there yet is made (from the old items, if any).
    private static func loaded() -> [String: String] {
        if let cache { return cache }
        var q = query(account)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: AnyObject?
        let status = SecItemCopyMatching(q as CFDictionary, &out)
        if status == errSecSuccess, let data = out as? Data,
           let keys = try? JSONDecoder().decode([String: String].self, from: data) {
            cache = keys
            return keys
        }
        guard status == errSecItemNotFound else { return [:] }     // denied or locked: ask again next launch
        let keys = migrate()
        cache = keys
        return keys
    }

    private static func save(_ keys: [String: String]) -> Bool {
        guard let data = try? JSONEncoder().encode(keys) else { return false }
        let q = query(account)
        let status = SecItemUpdate(q as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecSuccess { return true }
        guard status == errSecItemNotFound else { return false }
        var add = q
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
    }

    /// The old layout, one item per provider (account = provider id): read each once, save them as the one item,
    /// and delete the old ones only once the new one is saved.
    private static func migrate() -> [String: String] {
        var keys: [String: String] = [:]
        var found: [String] = []
        for p in ProviderPreset.all {
            var q = query(p.id)
            q[kSecReturnData as String] = true
            q[kSecMatchLimit as String] = kSecMatchLimitOne
            var out: AnyObject?
            let status = SecItemCopyMatching(q as CFDictionary, &out)
            guard status != errSecItemNotFound else { continue }
            found.append(p.id)
            if status == errSecSuccess, let data = out as? Data, let k = String(data: data, encoding: .utf8), !k.isEmpty {
                keys[p.id] = k
            }
        }
        // an old item that could not be read (denied) would be lost: keep the old layout and try again next launch
        guard !keys.isEmpty, keys.count == found.count else { return keys }
        if save(keys) {
            for id in found where keys[id] != nil { SecItemDelete(query(id) as CFDictionary) }
        }
        return keys
    }
}

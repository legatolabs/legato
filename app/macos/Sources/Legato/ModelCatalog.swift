import Foundation

/// Each provider's models, read from its OpenAI-style `GET {base}/models` with the person's key (Anthropic's needs its
/// own headers). OpenRouter's list is cut to the models that take images. Kept a day in AppSettings.catalog.
enum ModelCatalog {
    static let maxAge: TimeInterval = 24 * 3600

    /// Refresh every usable provider whose list is missing or older than a day (in the background).
    static func refreshStale(_ s: AppSettings) {
        for p in ProviderPreset.all where s.usable(p.id) {
            let fresh = s.catalogDate[p.id].map { Date().timeIntervalSince($0) < maxAge } ?? false
            if !fresh { refresh(p.id, s) }
        }
    }

    static func refresh(_ provider: String, _ s: AppSettings, done: ((String?) -> Void)? = nil) {
        let base = s.baseURL(for: provider).trimmingCharacters(in: CharacterSet(charactersIn: " /"))
        guard let url = URL(string: base + "/models") else { done?("not a URL: \(base)"); return }
        var req = URLRequest(url: url, timeoutInterval: 15)
        let key = s.key(for: provider)
        if provider == "anthropic" {
            req.setValue(key, forHTTPHeaderField: "x-api-key")
            req.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
        } else if !key.isEmpty {
            req.setValue("Bearer " + key, forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: req) { data, resp, err in
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            guard let data, err == nil, code == 200, let ids = parse(data, provider: provider) else {
                DispatchQueue.main.async { done?(err?.localizedDescription ?? "HTTP \(code)") }
                return
            }
            DispatchQueue.main.async {
                s.catalog[provider] = ids
                s.catalogDate[provider] = Date()
                done?(nil)
            }
        }.resume()
    }

    /// `{"data": [{"id": ...}]}` or `[{"id": ...}]`; OpenRouter's entries say which inputs a model takes.
    static func parse(_ data: Data, provider: String) -> [String]? {
        guard let json = try? JSONSerialization.jsonObject(with: data) else { return nil }
        let rows = (json as? [String: Any])?["data"] as? [[String: Any]] ?? (json as? [[String: Any]])
        guard let rows else { return nil }
        var ids: [String] = []
        for r in rows {
            guard let id = r["id"] as? String, !id.isEmpty else { continue }
            if provider == "openrouter", let arch = r["architecture"] as? [String: Any],
               let ins = arch["input_modalities"] as? [String], !ins.contains("image") { continue }
            if id.hasSuffix(":batch") || id.contains("embed") || id.contains("whisper") || id.contains("tts") { continue }
            ids.append(id.hasPrefix("models/") ? String(id.dropFirst(7)) : id)   // Gemini lists "models/<id>"
        }
        return ids
    }
}

import AppKit
import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var test: TestState
    let runTest: () -> Void
    @State private var key = ""
    @State private var keyShown = false
    @State private var listing = false
    @State private var listed: String?

    private func refreshModels() {
        listing = true
        listed = nil
        let id = settings.providerID
        ModelCatalog.refresh(id, settings) { err in
            listing = false
            let n = settings.catalog[id]?.count ?? 0
            listed = err.map { "Couldn’t list models: \($0)" } ?? "\(n) models from \(ProviderPreset.find(id).name). ⌘K in the command bar searches them."
        }
    }

    var body: some View {
        Form {
            Section("Model") {
                Picker("Provider", selection: Binding(get: { settings.providerID },
                                                      set: { id in settings.choose(ProviderPreset.find(id)); key = settings.apiKey })) {
                    ForEach(ProviderPreset.all) { Text($0.name).tag($0.id) }
                }
                TextField("Base URL", text: $settings.baseURL, prompt: Text("https://…/v1"))
                HStack {
                    TextField("Model", text: $settings.model, prompt: Text("model id"))
                    let known = settings.preset.models + (settings.catalog[settings.providerID] ?? []).filter { !settings.preset.models.contains($0) }
                    if !known.isEmpty {
                        Menu("") {
                            ForEach(known.prefix(200), id: \.self) { m in Button(m) { settings.model = m; settings.noteUsed() } }
                        }
                        .menuStyle(.borderlessButton)
                        .frame(width: 24)
                    }
                    Button(action: refreshModels) {
                        Image(systemName: listing ? "hourglass" : "arrow.clockwise")
                    }
                    .buttonStyle(.borderless)
                    .help("Read this provider's models with your key")
                    .disabled(listing)
                }
                if let listed {
                    Text(listed).font(.system(size: 11)).foregroundColor(.secondary)
                }
                HStack {
                    Group {
                        if keyShown {
                            TextField("API key", text: $key, prompt: Text(settings.preset.keyHint))
                        } else {
                            SecureField("API key", text: $key, prompt: Text(settings.preset.keyHint))
                        }
                    }
                    .onSubmit { settings.apiKey = key; refreshModels() }
                    .onChange(of: key) { _, v in settings.apiKey = v.trimmingCharacters(in: .whitespacesAndNewlines) }
                    Button(action: { keyShown.toggle() }) { Image(systemName: keyShown ? "eye.slash" : "eye") }
                        .buttonStyle(.borderless)
                }
                HStack {
                    Button(test.running ? "Testing…" : "Test connection", action: runTest)
                        .disabled(test.running)
                    if let r = test.result {
                        Image(systemName: test.ok ? "checkmark.circle.fill" : "xmark.octagon.fill")
                            .foregroundColor(test.ok ? .green : .red)
                        Text(r).font(.system(size: 11)).foregroundColor(.secondary).lineLimit(2)
                    }
                }
                Text("Each provider keeps its own key in your Keychain, sent only to its base URL. Screenshots of your screen go to the same place. Switch between models of any provider with a key from the command bar (⌘1-⌘4, ⌘K).")
                    .font(.system(size: 11)).foregroundColor(.secondary)
            }
            Section("Behaviour") {
                Stepper("At most \(settings.maxSteps) turns a task", value: $settings.maxSteps, in: 5...200, step: 5)
                Picker("Screenshots the model keeps", selection: $settings.imageMax) {
                    Text("4: faster").tag(4)
                    Text("8: more accurate").tag(8)
                }
                Text("Measured on Mac tasks: 4 is about a third faster and a quarter cheaper than 8, and passes about 5% fewer tasks.")
                    .font(.system(size: 11)).foregroundColor(.secondary)
                Toggle("Ask before clicking Send, Delete, Pay and the like", isOn: $settings.guardClicks)
                Toggle("Let the agent run commands and AppleScript (asks before anything it can’t undo)", isOn: $settings.allowShell)
                Toggle("Show the agent’s cursor", isOn: $settings.ghostCursor)
                Toggle("Open with a tap on Fn (⌥⇧Space always works)", isOn: $settings.fnKey)
            }
            Section("Advanced") {
                TextField("Engine Python", text: $settings.enginePython,
                          prompt: Text(EngineClient.pythonPath() ?? "path to a python with legato installed"))
                HStack {
                    Button("Show engine log") { NSWorkspace.shared.activateFileViewerSelecting([EngineClient.logURL]) }
                    Button("Show runs folder") {
                        try? FileManager.default.createDirectory(at: settings.runsDir, withIntermediateDirectories: true)
                        NSWorkspace.shared.open(settings.runsDir)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .fixedSize(horizontal: false, vertical: true)
        .onAppear { key = settings.apiKey }
    }
}

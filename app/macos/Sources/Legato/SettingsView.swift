import AppKit
import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var test: TestState
    let runTest: () -> Void
    @State private var key = ""
    @State private var keyShown = false

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
                    if !settings.preset.models.isEmpty {
                        Menu("") {
                            ForEach(settings.preset.models, id: \.self) { m in Button(m) { settings.model = m } }
                        }
                        .menuStyle(.borderlessButton)
                        .frame(width: 24)
                    }
                }
                HStack {
                    Group {
                        if keyShown {
                            TextField("API key", text: $key, prompt: Text(settings.preset.keyHint))
                        } else {
                            SecureField("API key", text: $key, prompt: Text(settings.preset.keyHint))
                        }
                    }
                    .onSubmit { settings.apiKey = key }
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
                Text("Your key is kept in your Keychain and sent only to this base URL. Screenshots of your screen go to the same place.")
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

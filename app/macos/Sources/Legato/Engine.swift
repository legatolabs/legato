import Foundation

/// The Python engine (`python -m legato app-engine`) as a child process, spoken to in JSON lines.
/// Being Legato.app's child, it uses Legato.app's Accessibility permission to post input.
final class EngineClient {
    typealias JSON = [String: Any]

    var onMessage: ((JSON) -> Void)?
    var onExit: ((String) -> Void)?
    private(set) var pid: pid_t = 0
    private(set) var running = false

    private var process: Process?
    private var stdin: FileHandle?
    private var buffer = Data()
    private let writeQueue = DispatchQueue(label: "legato.engine.write")
    private var log: FileHandle?
    private var stderrTail = ""

    static var logURL: URL {
        let dir = FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Logs/Legato", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("engine.log")
    }

    /// The Python that has legato installed: the setting, else the one bundled in the app, else the one build.sh found.
    static func pythonPath() -> String? {
        let fm = FileManager.default
        let setting = AppSettings.shared.enginePython.trimmingCharacters(in: .whitespaces)
        var candidates: [String] = []
        if !setting.isEmpty { candidates.append((setting as NSString).expandingTildeInPath) }
        if let res = Bundle.main.resourcePath { candidates.append(res + "/engine/bin/python3") }
        if let dev = Bundle.main.object(forInfoDictionaryKey: "LegatoEnginePython") as? String, !dev.isEmpty {
            candidates.append(dev)
        }
        return candidates.first { fm.isExecutableFile(atPath: $0) }
    }

    func start() throws {
        guard !running else { return }
        guard let python = EngineClient.pythonPath() else {
            throw NSError(domain: "Legato", code: 1, userInfo: [NSLocalizedDescriptionKey:
                "No engine found. Set the Python that has legato installed in Settings → Advanced, or rebuild with build.sh."])
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-u", "-m", "legato", "app-engine"]
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["LEGATO_CONFIRM"] = "off"                     // the app asks; the engine never reads a terminal
        if let res = Bundle.main.resourcePath, python.hasPrefix(res) {
            env["PYTHONDONTWRITEBYTECODE"] = "1"          // the bundled engine: never write into the signed app
            env["PYTHONNOUSERSITE"] = "1"                 // nor read the person's own Python packages
            env.removeValue(forKey: "PYTHONPATH")
            env.removeValue(forKey: "PYTHONHOME")
        }
        p.environment = env
        let inPipe = Pipe(), outPipe = Pipe(), errPipe = Pipe()
        p.standardInput = inPipe
        p.standardOutput = outPipe
        p.standardError = errPipe

        FileManager.default.createFile(atPath: EngineClient.logURL.path, contents: nil)
        log = try? FileHandle(forWritingTo: EngineClient.logURL)
        log?.seekToEndOfFile()
        log?.write("\n--- engine start \(Date()) \(python)\n".data(using: .utf8)!)

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard let self, !data.isEmpty else { return }
            self.consume(data)
        }
        errPipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard let self, !data.isEmpty else { return }
            self.log?.write(data)
            if let s = String(data: data, encoding: .utf8) {
                DispatchQueue.main.async { self.stderrTail = String((self.stderrTail + s).suffix(1500)) }
            }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self else { return }
                self.running = false
                self.pid = 0
                let tail = self.stderrTail.trimmingCharacters(in: .whitespacesAndNewlines)
                self.onExit?("The engine stopped (exit \(proc.terminationStatus)). \(tail.split(separator: "\n").last.map(String.init) ?? "")")
            }
        }
        try p.run()
        process = p
        stdin = inPipe.fileHandleForWriting
        pid = p.processIdentifier
        running = true
    }

    func stop() {
        stdin?.closeFile()                                  // the engine stops its run and exits on end of input
        if let p = process, p.isRunning {
            DispatchQueue.global().asyncAfter(deadline: .now() + 3) { if p.isRunning { p.terminate() } }
        }
        running = false
    }

    func send(_ msg: JSON) {
        guard let stdin, running,
              var data = try? JSONSerialization.data(withJSONObject: msg, options: []) else { return }
        data.append(0x0A)
        writeQueue.async { try? stdin.write(contentsOf: data) }
    }

    func reply(_ id: Int, _ payload: JSON) {
        var msg = payload
        msg["type"] = "reply"
        msg["id"] = id
        send(msg)
    }

    private func consume(_ data: Data) {
        buffer.append(data)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            guard !line.isEmpty,
                  let obj = try? JSONSerialization.jsonObject(with: line) as? JSON else { continue }
            DispatchQueue.main.async { [weak self] in self?.onMessage?(obj) }
        }
    }
}

import AVFoundation
import Speech

/// Hold Fn to talk: speech recognition (on this Mac when it can) while Fn is held. `onText` gets the words as they
/// come; `finish` stops listening and hands over the final words.
final class VoiceInput {
    private let audio = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private let recognizer = SFSpeechRecognizer(locale: Locale.current) ?? SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var pending: ((String) -> Void)?
    private(set) var text = ""
    private(set) var running = false
    var onText: ((String) -> Void)?

    /// Whether both permissions are already given (no prompt would show).
    static var authorized: Bool {
        SFSpeechRecognizer.authorizationStatus() == .authorized && AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
    }

    /// Ask for Speech Recognition, then the Microphone. `done` gets nil when both are allowed, else what to do.
    static func authorize(_ done: @escaping (String?) -> Void) {
        SFSpeechRecognizer.requestAuthorization { st in
            guard st == .authorized else {
                DispatchQueue.main.async { done("Allow Speech Recognition for Legato in System Settings → Privacy & Security.") }
                return
            }
            AVCaptureDevice.requestAccess(for: .audio) { ok in
                DispatchQueue.main.async { done(ok ? nil : "Allow the Microphone for Legato in System Settings → Privacy & Security.") }
            }
        }
    }

    func start() throws {
        stop()
        guard let recognizer, recognizer.isAvailable else {
            throw NSError(domain: "Legato", code: 7, userInfo: [NSLocalizedDescriptionKey: "Speech recognition isn’t available."])
        }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.addsPunctuation = true
        if recognizer.supportsOnDeviceRecognition { req.requiresOnDeviceRecognition = true }   // speech stays here
        request = req
        text = ""
        let input = audio.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { [weak req] buf, _ in
            req?.append(buf)
        }
        audio.prepare()
        try audio.start()
        running = true
        task = recognizer.recognitionTask(with: req) { [weak self] result, error in
            DispatchQueue.main.async {
                guard let self else { return }
                if let result {
                    self.text = result.bestTranscription.formattedString
                    self.onText?(self.text)
                }
                if result?.isFinal == true || error != nil { self.deliver() }
            }
        }
    }

    /// Stop listening and get the final words (the recognizer's last result, or what it had after 0.6 s).
    func finish(_ done: @escaping (String) -> Void) {
        guard running else { done(text); return }
        pending = done
        audio.inputNode.removeTap(onBus: 0)
        audio.stop()
        running = false
        request?.endAudio()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in self?.deliver() }
    }

    private func deliver() {
        guard let done = pending else { return }
        pending = nil
        task?.cancel()
        task = nil
        request = nil
        done(text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    func stop() {
        pending = nil
        if running {
            audio.inputNode.removeTap(onBus: 0)
            audio.stop()
            running = false
        }
        task?.cancel()
        task = nil
        request?.endAudio()
        request = nil
    }
}

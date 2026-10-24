import AudioToolbox
import AVFoundation
import Speech

/// Speaking into the AirPods and transcribing what the user says, on device.
final class SpeechIO: NSObject, AVSpeechSynthesizerDelegate {
    private let synth = AVSpeechSynthesizer()
    private var spoken: (() -> Void)?

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var recognition: SFSpeechRecognitionTask?
    private var silenceTimer: Timer?
    private var hardStop: Timer?

    override init() {
        super.init()
        synth.delegate = self
    }

    func requestPermissions(_ done: @escaping (Bool) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            AVAudioApplication.requestRecordPermission { granted in
                done(granted && status == .authorized)
            }
        }
    }

    // MARK: speaking

    func say(_ text: String, done: @escaping () -> Void) {
        spoken = done
        let utterance = AVSpeechUtterance(string: text)
        utterance.rate = 0.52
        utterance.volume = 0.8  // a whisper, not an announcement
        synth.speak(utterance)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let done = spoken
        spoken = nil
        done?()
    }

    // MARK: listening

    /// Transcribes until the user pauses, or `maxSeconds` pass. Calls back once.
    func listen(maxSeconds: Double, done: @escaping (String) -> Void) {
        stopListening()
        guard let recognizer, recognizer.isAvailable else { return done("") }
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
        self.request = request

        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }
        engine.prepare()
        do { try engine.start() } catch { return done("") }

        var best = ""
        var finished = false
        let finish = { [weak self] in
            guard !finished else { return }
            finished = true
            self?.stopListening()
            done(best)
        }
        recognition = recognizer.recognitionTask(with: request) { [weak self] result, error in
            if let result {
                best = result.bestTranscription.formattedString
                DispatchQueue.main.async {
                    self?.silenceTimer?.invalidate()
                    self?.silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.2, repeats: false) { _ in finish() }
                }
                if result.isFinal { DispatchQueue.main.async { finish() } }
            }
            if error != nil { DispatchQueue.main.async { finish() } }
        }
        hardStop = Timer.scheduledTimer(withTimeInterval: maxSeconds, repeats: false) { _ in finish() }
    }

    func stopListening() {
        silenceTimer?.invalidate()
        hardStop?.invalidate()
        if engine.isRunning {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        recognition?.cancel()
        request = nil
        recognition = nil
    }

    static func yesNo(_ text: String) -> String? {
        let t = text.lowercased()
        if ["yes", "yeah", "yep", "sure", "do it", "okay", "ok"].contains(where: t.contains) { return "yes" }
        if ["no", "nope", "don't", "not now", "cancel"].contains(where: t.contains) { return "no" }
        return nil
    }
}

/// Short sounds in the AirPods so the user knows what state they're in.
enum Earcon {
    case listening, confirm, dismiss

    static func play(_ e: Earcon) {
        let id: SystemSoundID = switch e {
        case .listening: 1113
        case .confirm: 1111
        case .dismiss: 1112
        }
        AudioServicesPlaySystemSound(id)
    }
}

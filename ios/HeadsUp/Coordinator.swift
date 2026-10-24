import AVFoundation
import SwiftUI

/// Wires the pieces together: backend socket in, voice and haptics out,
/// nods and speech back.
@MainActor
final class Coordinator: ObservableObject {
    @AppStorage("backendURL") var backendURL = "ws://192.168.1.10:8000"
    @AppStorage("userId") var userId = "vibhu"

    @Published var connected = false
    @Published var airpodsIn = false
    @Published var listening = false
    @Published var lastHeard = "—"

    private let backend = BackendClient()
    private let voice = VoiceLoop()
    private let speech = SpeechIO()
    private let gestures = HeadGestures()
    private let watch = WatchRelay()
    private let route = AudioRouteMonitor()
    private let calendar = CalendarSync()
    private var busy = false  // speaking or listening; the wake word is paused meanwhile
    private var currentAsk: String?  // the task whose yes/no we are waiting for

    func start() {
        configureAudioSession()
        backend.onMessage = { [weak self] msg in Task { @MainActor in self?.handle(msg) } }
        backend.onStatus = { [weak self] up in
            Task { @MainActor in
                self?.connected = up
                if up { self?.calendar.send() }  // the backend keeps calendars in memory; resend on every connect
            }
        }
        calendar.onEvents = { [weak self] events in
            Task { @MainActor in self?.backend.send(CalendarMessage(events: events)) }
        }
        route.onChange = { [weak self] inEar in
            Task { @MainActor in
                self?.airpodsIn = inEar
                self?.backend.send(ContextMessage(airpodsIn: inEar, inMeeting: nil))
            }
        }
        watch.onAnswer = { [weak self] taskId, value in
            Task { @MainActor in self?.backend.send(AnswerMessage(taskId: taskId, value: value, via: "double_tap")) }
        }
        voice.onWake = { [weak self] in Task { @MainActor in self?.handleWake() } }
        watch.activate()
        route.start()
        calendar.start()
        speech.requestPermissions { [weak self] ok in
            Task { @MainActor in
                guard let self, ok else { return }
                self.listening = self.voice.start()
            }
        }
        connect()
    }

    func connect() {
        guard let url = URL(string: "\(backendURL)/ws/device/\(userId)") else { return }
        backend.connect(url)
    }

    private func configureAudioSession() {
        let session = AVAudioSession.sharedInstance()
        // .allowBluetooth routes the mic through the AirPods (hands-free profile).
        try? session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .duckOthers])
        try? session.setActive(true)
    }

    // MARK: backend -> person

    private func handle(_ msg: Inbound) {
        switch msg.type {
        case "haptic":
            if let pattern = msg.pattern { watch.play(pattern) }
        case "speak":
            guard let text = msg.text else { return }
            if msg.expect == "yes_no", let taskId = msg.taskId {
                askYesNo(text, taskId: taskId, timeout: msg.timeoutS ?? 20)
            } else {
                say(text)
            }
        case "approve":
            // In a meeting: two taps on the wrist, approve with a double-tap pinch.
            if let text = msg.text, let taskId = msg.taskId { watch.requestApproval(text, taskId: taskId) }
        default:
            break
        }
    }

    private func say(_ text: String, then: (() -> Void)? = nil) {
        busy = true
        voice.pause()
        speech.say(text) { [weak self] in
            Task { @MainActor in
                self?.busy = false
                self?.voice.resume()
                then?()
            }
        }
    }

    private func askYesNo(_ text: String, taskId: String, timeout: Double) {
        busy = true
        voice.pause()
        speech.say(text) { [weak self] in
            Task { @MainActor in self?.collectAnswer(taskId: taskId, timeout: timeout) }
        }
    }

    /// A nod, a head shake or a spoken yes/no, whichever comes first.
    private func collectAnswer(taskId: String, timeout: Double) {
        currentAsk = taskId
        let finish: (String, String) -> Void = { [weak self] value, via in
            Task { @MainActor in
                guard let self, self.currentAsk == taskId else { return }
                self.currentAsk = nil
                self.gestures.stop()
                self.speech.stopListening()
                self.backend.send(AnswerMessage(taskId: taskId, value: value, via: via))
                Earcon.play(value == "yes" ? .confirm : .dismiss)
                self.busy = false
                self.voice.resume()
            }
        }
        gestures.awaitYesNo(timeout: timeout) { value in finish(value, value == "yes" ? "nod" : value == "no" ? "shake" : "timeout") }
        speech.listen(maxSeconds: timeout) { text in
            if let value = SpeechIO.yesNo(text) { finish(value, "voice") }
        }
    }

    // MARK: person -> backend

    private func handleWake() {
        guard !busy else { return }
        busy = true
        voice.pause()
        Earcon.play(.listening)
        speech.listen(maxSeconds: 6) { [weak self] text in
            Task { @MainActor in
                guard let self else { return }
                self.lastHeard = text.isEmpty ? "—" : text
                if !text.isEmpty { self.backend.send(WakeMessage(transcript: text)) }
                self.busy = false
                self.voice.resume()
            }
        }
    }
}

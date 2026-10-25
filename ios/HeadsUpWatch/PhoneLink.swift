import Foundation
import WatchConnectivity

/// Watch side of the link: plays haptics and shows approvals, and sends answers back.
///
/// Two ways to reach the backend:
/// - Through the iPhone app (normal): the phone relays haptics and approvals over WatchConnectivity.
/// - Direct mode: the watch connects to the backend's /ws/watch/{user} itself. Used in the
///   watch simulator (no iPhone needed) and as a fallback. On by default only in the simulator,
///   so a real watch doesn't get every tap twice.
@MainActor
final class PhoneLink: NSObject, ObservableObject {
    struct Ask { let taskId: String; let text: String }

    @Published var pendingApproval: Ask?
    @Published var lastPattern: HapticPattern?
    @Published var lastText: String?
    @Published var directConnected = false

    @Published var directEnabled: Bool { didSet { save(); reconnect() } }
    @Published var backendURL: String { didSet { save() } }
    @Published var userId: String { didSet { save() } }

    private let haptics = HapticPlayer()
    private let direct = BackendClient()

    override init() {
        let defaults = UserDefaults.standard
        #if targetEnvironment(simulator)
        let directByDefault = true
        #else
        let directByDefault = false
        #endif
        directEnabled = defaults.object(forKey: "directEnabled") as? Bool ?? directByDefault
        backendURL = defaults.string(forKey: "backendURL") ?? "ws://localhost:8000"
        userId = defaults.string(forKey: "userId") ?? "vibhu"
        super.init()
    }

    func activate() {
        if WCSession.isSupported() {
            WCSession.default.delegate = self
            WCSession.default.activate()
        }
        direct.onMessage = { [weak self] msg in Task { @MainActor in self?.receive(msg) } }
        direct.onStatus = { [weak self] up in Task { @MainActor in self?.directConnected = up } }
        reconnect()
    }

    /// Apply the direct-mode settings: connect, reconnect to a new URL, or disconnect.
    func reconnect() {
        direct.disconnect()
        guard directEnabled, let url = URL(string: "\(backendURL)/ws/watch/\(userId)") else { return }
        direct.connect(url)
    }

    func answer(_ taskId: String, _ value: String) {
        pendingApproval = nil
        if directEnabled && directConnected {
            direct.send(AnswerMessage(taskId: taskId, value: value, via: "double_tap"))
            return
        }
        let payload: [String: Any] = [WatchKey.taskId: taskId, WatchKey.answer: value]
        let session = WCSession.default
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil) { _ in session.transferUserInfo(payload) }
        } else {
            session.transferUserInfo(payload)
        }
    }

    private func save() {
        let defaults = UserDefaults.standard
        defaults.set(directEnabled, forKey: "directEnabled")
        defaults.set(backendURL, forKey: "backendURL")
        defaults.set(userId, forKey: "userId")
    }

    private func play(_ pattern: HapticPattern) {
        lastPattern = pattern
        haptics.play(pattern)
    }

    /// From the iPhone app, over WatchConnectivity.
    fileprivate func receive(_ payload: [String: Any]) {
        if let raw = payload[WatchKey.haptic] as? String, let pattern = HapticPattern(rawValue: raw) {
            play(pattern)
        }
        if let text = payload[WatchKey.approve] as? String, let taskId = payload[WatchKey.taskId] as? String {
            pendingApproval = Ask(taskId: taskId, text: text)
        }
    }

    /// From the backend directly. The watch has no AirPods, so a spoken yes/no question
    /// is shown as an approval on the screen instead.
    private func receive(_ msg: Inbound) {
        switch msg.type {
        case "haptic":
            if let pattern = msg.pattern { play(pattern) }
        case "approve":
            if let taskId = msg.taskId, let text = msg.text { pendingApproval = Ask(taskId: taskId, text: text) }
        case "speak":
            if msg.expect == "yes_no", let taskId = msg.taskId, let text = msg.text {
                pendingApproval = Ask(taskId: taskId, text: text)
            } else {
                lastText = msg.text
            }
        default:
            break
        }
    }
}

extension PhoneLink: WCSessionDelegate {
    nonisolated func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState,
                             error: Error?) {}

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in self.receive(message) }
    }

    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        Task { @MainActor in self.receive(userInfo) }
    }
}

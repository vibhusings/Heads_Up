import WatchConnectivity

/// Watch side of the link: plays haptics the phone sends and returns approvals.
@MainActor
final class PhoneLink: NSObject, ObservableObject {
    struct Ask { let taskId: String; let text: String }

    @Published var pendingApproval: Ask?
    @Published var lastPattern: HapticPattern?
    private let haptics = HapticPlayer()

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func answer(_ taskId: String, _ value: String) {
        pendingApproval = nil
        let payload: [String: Any] = [WatchKey.taskId: taskId, WatchKey.answer: value]
        let session = WCSession.default
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil) { _ in session.transferUserInfo(payload) }
        } else {
            session.transferUserInfo(payload)
        }
    }

    fileprivate func receive(_ payload: [String: Any]) {
        if let raw = payload[WatchKey.haptic] as? String, let pattern = HapticPattern(rawValue: raw) {
            lastPattern = pattern
            haptics.play(pattern)
        }
        if let text = payload[WatchKey.approve] as? String, let taskId = payload[WatchKey.taskId] as? String {
            pendingApproval = Ask(taskId: taskId, text: text)
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

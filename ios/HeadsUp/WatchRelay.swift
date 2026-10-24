import WatchConnectivity

/// Phone side of the watch link: sends haptic patterns and approval requests,
/// receives double-tap answers.
final class WatchRelay: NSObject, WCSessionDelegate {
    var onAnswer: ((_ taskId: String, _ value: String) -> Void)?

    func activate() {
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
    }

    func play(_ pattern: HapticPattern) {
        deliver([WatchKey.haptic: pattern.rawValue])
    }

    func requestApproval(_ text: String, taskId: String) {
        deliver([WatchKey.haptic: HapticPattern.decision.rawValue, WatchKey.approve: text, WatchKey.taskId: taskId])
    }

    /// Live message when the watch app is running; queued delivery otherwise.
    private func deliver(_ payload: [String: Any]) {
        let session = WCSession.default
        guard session.activationState == .activated, session.isWatchAppInstalled else { return }
        if session.isReachable {
            session.sendMessage(payload, replyHandler: nil) { _ in session.transferUserInfo(payload) }
        } else {
            session.transferUserInfo(payload)
        }
    }

    func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        if let taskId = message[WatchKey.taskId] as? String, let value = message[WatchKey.answer] as? String {
            onAnswer?(taskId, value)
        }
    }

    func session(_ session: WCSession, activationDidCompleteWith state: WCSessionActivationState, error: Error?) {}
    func sessionDidBecomeInactive(_ session: WCSession) {}
    func sessionDidDeactivate(_ session: WCSession) { session.activate() }
}

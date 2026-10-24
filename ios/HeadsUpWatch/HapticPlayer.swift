import WatchKit

/// The vibration vocabulary. Each meaning is a timed sequence of built-in
/// haptics, distinct enough to read without looking.
final class HapticPlayer {
    private let device = WKInterfaceDevice.current()

    func play(_ pattern: HapticPattern) {
        switch pattern {
        case .handled:   // one soft tap: "I handled something quietly"
            device.play(.click)
        case .decision:  // two taps: "I need a decision, I'll speak in a moment"
            sequence([.directionUp, .directionUp], gap: 0.35)
        case .leaveNow:  // rising pulses: "leave now"
            pulses(gaps: [1.2, 0.9, 0.6, 0.4, 0.25])
        case .urgent:    // long buzz: "something broke and I couldn't fix it"
            sequence([.failure, .notification, .failure], gap: 0.25)
        }
    }

    private func sequence(_ types: [WKHapticType], gap: TimeInterval) {
        for (i, type) in types.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + gap * Double(i)) { self.device.play(type) }
        }
    }

    private func pulses(gaps: [TimeInterval]) {
        var t: TimeInterval = 0
        for gap in gaps {
            t += gap
            DispatchQueue.main.asyncAfter(deadline: .now() + t) { self.device.play(.click) }
        }
    }
}

import AVFoundation

/// Tells the backend whether AirPods are in, so it can pick whisper vs. phone call.
final class AudioRouteMonitor {
    var onChange: ((Bool) -> Void)?
    private var last: Bool?

    func start() {
        NotificationCenter.default.addObserver(forName: AVAudioSession.routeChangeNotification,
                                               object: nil, queue: .main) { [weak self] _ in self?.check() }
        check()
    }

    private func check() {
        let outputs = AVAudioSession.sharedInstance().currentRoute.outputs
        let inEar = outputs.contains { port in
            [.bluetoothHFP, .bluetoothA2DP, .bluetoothLE].contains(port.portType)
                && port.portName.localizedCaseInsensitiveContains("AirPods")
        }
        if inEar != last {
            last = inEar
            onChange?(inEar)
        }
    }
}

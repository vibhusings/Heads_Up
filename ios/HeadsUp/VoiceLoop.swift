import Foundation
import MediaPlayer
import Porcupine

/// "Hey Heads Up" wake word on the AirPods mic, with the stem squeeze as a backup.
///
/// Train the keyword for free in the Picovoice Console, download the iOS
/// `.ppn` file, add it to the app target as `hey-heads-up_ios.ppn`, and put
/// your access key in Secrets.swift.
final class VoiceLoop {
    var onWake: (() -> Void)?
    private var porcupine: PorcupineManager?
    private var paused = false

    /// Returns true when the wake word is listening.
    func start() -> Bool {
        registerSqueeze()
        guard let path = Bundle.main.path(forResource: "hey-heads-up_ios", ofType: "ppn") else {
            print("VoiceLoop: wake word file missing; only the stem squeeze will work")
            return false
        }
        do {
            porcupine = try PorcupineManager(accessKey: Secrets.picovoiceAccessKey, keywordPath: path,
                                             onDetection: { [weak self] _ in self?.onWake?() })
            try porcupine?.start()
            return true
        } catch {
            print("VoiceLoop: \(error)")
            return false
        }
    }

    /// Free the mic while we speak or transcribe.
    func pause() {
        guard !paused else { return }
        paused = true
        try? porcupine?.stop()
    }

    func resume() {
        guard paused else { return }
        paused = false
        try? porcupine?.start()
    }

    /// A squeeze on the AirPods stem arrives as play/pause while this app owns
    /// the audio session. iOS only routes it here if we are the "now playing"
    /// app, so this is a best-effort backup for loud rooms.
    private func registerSqueeze() {
        let center = MPRemoteCommandCenter.shared()
        center.togglePlayPauseCommand.isEnabled = true
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            self?.onWake?()
            return .success
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [MPMediaItemPropertyTitle: "Heads Up"]
    }
}

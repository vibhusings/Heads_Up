import CoreMotion

/// Nod = yes, shake = no, from AirPods head motion
/// (AirPods Pro, AirPods Max, AirPods 3rd generation or newer).
final class HeadGestures {
    private let motion = CMHeadphoneMotionManager()
    private var samples: [(pitch: Double, yaw: Double)] = []
    private var done: ((String) -> Void)?
    private var timeout: DispatchWorkItem?

    /// Rotation speed that counts as deliberate. Tune this on the AirPods you demo with.
    var threshold = 1.2  // radians per second

    func awaitYesNo(timeout seconds: Double, _ completion: @escaping (String) -> Void) {
        stop()
        done = completion
        let item = DispatchWorkItem { [weak self] in self?.finish("none") }
        timeout = item
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: item)
        guard motion.isDeviceMotionAvailable else { return }  // no head tracking: spoken answers still work
        motion.startDeviceMotionUpdates(to: .main) { [weak self] m, _ in
            guard let self, let r = m?.rotationRate else { return }
            self.samples.append((r.x, r.z))  // x: nodding axis, z: shaking axis
            if self.samples.count > 40 { self.samples.removeFirst() }
            if let gesture = self.classify() { self.finish(gesture) }
        }
    }

    func stop() {
        motion.stopDeviceMotionUpdates()
        timeout?.cancel()
        samples.removeAll()
        done = nil
    }

    private func finish(_ value: String) {
        let completion = done
        stop()
        completion?(value)
    }

    /// A gesture is rotation past the threshold that reverses direction at least three times.
    private func classify() -> String? {
        func reversals(_ values: [Double]) -> Int {
            var last = 0, count = 0
            for v in values where abs(v) > threshold {
                let sign = v > 0 ? 1 : -1
                if sign != last { count += 1; last = sign }
            }
            return count
        }
        let nod = reversals(samples.map(\.pitch))
        let shake = reversals(samples.map(\.yaw))
        if nod >= 3 && nod > shake { return "yes" }
        if shake >= 3 && shake > nod { return "no" }
        return nil
    }
}

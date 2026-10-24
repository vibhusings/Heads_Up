import HealthKit

/// watchOS only plays custom haptic sequences while the app is running. A
/// workout session keeps it running with the wrist down. Demo-only approach:
/// a shipping app would use notifications or an extended runtime session.
final class WorkoutKeeper: NSObject {
    private let store = HKHealthStore()
    private var session: HKWorkoutSession?

    func start() {
        guard HKHealthStore.isHealthDataAvailable() else { return }
        store.requestAuthorization(toShare: [HKObjectType.workoutType()], read: []) { [weak self] ok, _ in
            guard ok, let self else { return }
            let config = HKWorkoutConfiguration()
            config.activityType = .other
            config.locationType = .indoor
            do {
                let session = try HKWorkoutSession(healthStore: self.store, configuration: config)
                self.session = session
                session.startActivity(with: Date())
            } catch {
                print("WorkoutKeeper: \(error)")
            }
        }
    }

    func stop() {
        session?.end()
        session = nil
    }
}

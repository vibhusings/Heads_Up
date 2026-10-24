import EventKit
import Foundation

/// Reads the next 7 days from every calendar on the phone (iCloud, Google,
/// Outlook...) and sends them to the backend: on connect, and whenever the
/// calendar changes. Read-only; only titles, times and locations are sent.
final class CalendarSync {
    var onEvents: (([CalendarEvent]) -> Void)?
    private let store = EKEventStore()
    private var granted = false

    private static let day: DateFormatter = formatter("yyyy-MM-dd")
    private static let time: DateFormatter = formatter("HH:mm")

    private static func formatter(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = .current
        f.dateFormat = format
        return f
    }

    func start() {
        Task {
            granted = (try? await store.requestFullAccessToEvents()) ?? false
            guard granted else { return print("CalendarSync: access denied; meeting and conflict checks are off") }
            NotificationCenter.default.addObserver(forName: .EKEventStoreChanged, object: store, queue: .main) {
                [weak self] _ in self?.send()
            }
            send()
        }
    }

    /// Call again after reconnecting to the backend.
    func send() {
        guard granted else { return }
        let start = Calendar.current.startOfDay(for: Date())
        guard let end = Calendar.current.date(byAdding: .day, value: 7, to: start) else { return }
        let predicate = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        let events = store.events(matching: predicate).map { e -> CalendarEvent in
            let sameDay = Calendar.current.isDate(e.startDate, inSameDayAs: e.endDate)
            return CalendarEvent(
                id: e.eventIdentifier ?? UUID().uuidString,
                title: e.title ?? "Busy",
                date: Self.day.string(from: e.startDate),
                start: Self.time.string(from: e.startDate),
                end: sameDay ? Self.time.string(from: e.endDate) : "23:59",
                location: e.location ?? "",
                allDay: e.isAllDay
            )
        }
        onEvents?(events)
    }
}

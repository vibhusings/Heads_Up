import Foundation

/// The four meanings the watch can tap out. Raw values match the backend.
enum HapticPattern: String, Codable {
    case handled
    case decision
    case leaveNow = "leave_now"
    case urgent
}

/// A message from the backend to the phone.
struct Inbound: Decodable {
    let type: String            // "haptic" | "speak" | "approve"
    let pattern: HapticPattern?
    let text: String?
    let expect: String?         // "yes_no" | "none"
    let timeoutS: Double?
    let taskId: String?
    let channel: String?

    enum CodingKeys: String, CodingKey {
        case type, pattern, text, expect, channel
        case timeoutS = "timeout_s"
        case taskId = "task_id"
    }
}

/// Messages from the phone to the backend.
struct AnswerMessage: Encodable {
    let type = "answer"
    let taskId: String
    let value: String           // "yes" | "no" | "none"
    let via: String             // "nod" | "shake" | "voice" | "double_tap" | "timeout"
    enum CodingKeys: String, CodingKey { case type, value, via; case taskId = "task_id" }
}

struct WakeMessage: Encodable {
    let type = "wake"
    let transcript: String
}

struct ContextMessage: Encodable {
    let type = "context"
    let airpodsIn: Bool?
    let inMeeting: Bool?
    enum CodingKeys: String, CodingKey { case type; case airpodsIn = "airpods_in"; case inMeeting = "in_meeting" }
}

/// One calendar event, in the phone's local time. Matches backend `calendar_sync.parse_events`.
struct CalendarEvent: Encodable {
    let id: String
    let title: String
    let date: String     // "YYYY-MM-DD"
    let start: String    // "HH:MM"
    let end: String      // "HH:MM"
    let location: String
    let allDay: Bool
    enum CodingKeys: String, CodingKey { case id, title, date, start, end, location; case allDay = "all_day" }
}

struct CalendarMessage: Encodable {
    let type = "calendar"
    let events: [CalendarEvent]
}

/// Keys used between the phone and the watch over WatchConnectivity.
enum WatchKey {
    static let haptic = "haptic"
    static let approve = "approve"
    static let taskId = "task_id"
    static let answer = "answer"
}

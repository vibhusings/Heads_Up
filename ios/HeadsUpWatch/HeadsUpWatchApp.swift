import SwiftUI

@main
struct HeadsUpWatchApp: App {
    @StateObject private var link = PhoneLink()
    private let keeper = WorkoutKeeper()

    var body: some Scene {
        WindowGroup {
            WatchHome()
                .environmentObject(link)
                .task {
                    link.activate()
                    keeper.start()  // keeps the app alive so custom haptics can play with the wrist down
                }
        }
    }
}

struct WatchHome: View {
    @EnvironmentObject var link: PhoneLink

    var body: some View {
        if let ask = link.pendingApproval {
            ApprovalView(text: ask.text) { value in link.answer(ask.taskId, value) }
        } else {
            VStack(spacing: 6) {
                Image(systemName: "ear")
                    .font(.title2)
                Text("Heads Up")
                    .font(.headline)
                Text(link.lastPattern.map { "Last: \($0.rawValue.replacingOccurrences(of: "_", with: " "))" } ?? "Listening")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }
}

/// Shown only while a decision is pending in a meeting. Answers work without looking:
/// - any watch: double-tap anywhere on the screen to approve, long-press to decline
/// - Series 9 / Ultra 2 or newer: the finger-pinch double tap also approves
struct ApprovalView: View {
    let text: String
    let answer: (String) -> Void

    var body: some View {
        VStack(spacing: 8) {
            Text(text)
                .font(.footnote)
                .multilineTextAlignment(.center)
            Text("Double-tap to approve · hold to decline")
                .font(.caption2)
                .foregroundStyle(.secondary)
            Button("Approve") { answer("yes") }
                .handGestureShortcut(.primaryAction)  // no effect on Series 8 and older
                .tint(.green)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(Rectangle())
        .onTapGesture(count: 2) { answer("yes") }
        .onLongPressGesture(minimumDuration: 0.8) { answer("no") }
    }
}

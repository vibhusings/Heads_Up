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
            NavigationStack {
                VStack(spacing: 6) {
                    Image(systemName: "ear")
                        .font(.title2)
                    Text("Heads Up")
                        .font(.headline)
                    Text(link.lastPattern.map { "Last: \($0.rawValue.replacingOccurrences(of: "_", with: " "))" } ?? "Listening")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if let text = link.lastText {
                        Text(text)
                            .font(.caption2)
                            .multilineTextAlignment(.center)
                            .lineLimit(3)
                    }
                    if link.directEnabled {
                        Label(link.directConnected ? "Backend connected" : "Connecting…",
                              systemImage: link.directConnected ? "wifi" : "wifi.exclamationmark")
                            .font(.caption2)
                            .foregroundStyle(link.directConnected ? .green : .orange)
                    }
                }
                .toolbar {
                    ToolbarItem(placement: .topBarTrailing) {
                        NavigationLink { WatchSettings() } label: { Image(systemName: "gearshape") }
                    }
                }
            }
        }
    }
}

/// Direct mode: connect the watch to the backend itself (simulator, or no iPhone app).
struct WatchSettings: View {
    @EnvironmentObject var link: PhoneLink
    @State private var url = ""
    @State private var user = ""

    var body: some View {
        Form {
            Toggle("Direct to backend", isOn: $link.directEnabled)
            TextField("ws://localhost:8000", text: $url)
                .textInputAutocapitalization(.never)
            TextField("User", text: $user)
                .textInputAutocapitalization(.never)
            Button("Connect") {
                link.backendURL = url
                link.userId = user
                link.reconnect()
            }
            .disabled(!link.directEnabled)
            Text(link.directConnected ? "Connected" : "Not connected")
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .onAppear {
            url = link.backendURL
            user = link.userId
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
                .lineLimit(6)
                .minimumScaleFactor(0.8)
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

import SwiftUI

@main
struct HeadsUpApp: App {
    @StateObject private var coordinator = Coordinator()

    var body: some Scene {
        WindowGroup {
            SetupView()
                .environmentObject(coordinator)
                .task { coordinator.start() }
        }
    }
}

/// The only screen, used once for setup. Everything after that is voice, nods and taps.
struct SetupView: View {
    @EnvironmentObject var coordinator: Coordinator

    var body: some View {
        Form {
            Section("Backend") {
                TextField("ws://your-laptop.local:8000", text: $coordinator.backendURL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("User id", text: $coordinator.userId)
                    .textInputAutocapitalization(.never)
                Button("Connect") { coordinator.connect() }
            }
            Section("Status") {
                LabeledContent("Backend", value: coordinator.connected ? "connected" : "offline")
                LabeledContent("AirPods", value: coordinator.airpodsIn ? "in" : "not detected")
                LabeledContent("Wake word", value: coordinator.listening ? "listening" : "off")
                LabeledContent("Last heard", value: coordinator.lastHeard)
            }
            Section {
                Text("Put your AirPods in, lock the phone, and say \"Hey Heads Up\".")
                    .foregroundStyle(.secondary)
            }
        }
    }
}

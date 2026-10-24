import Foundation

/// WebSocket to the Python backend, with automatic reconnect.
final class BackendClient {
    var onMessage: ((Inbound) -> Void)?
    var onStatus: ((Bool) -> Void)?

    private var task: URLSessionWebSocketTask?
    private var url: URL?
    private var retryDelay: TimeInterval = 1
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()

    func connect(_ url: URL) {
        self.url = url
        task?.cancel(with: .goingAway, reason: nil)
        let t = URLSession.shared.webSocketTask(with: url)
        task = t
        t.resume()
        receive(on: t)
        t.sendPing { [weak self] error in
            self?.onStatus?(error == nil)
            if error == nil { self?.retryDelay = 1 }
        }
    }

    func send<T: Encodable>(_ message: T) {
        guard let task, let data = try? encoder.encode(message),
              let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { _ in }
    }

    private func receive(on t: URLSessionWebSocketTask) {
        t.receive { [weak self] result in
            guard let self, t === self.task else { return }
            switch result {
            case .success(.string(let text)):
                if let data = text.data(using: .utf8), let msg = try? self.decoder.decode(Inbound.self, from: data) {
                    self.onMessage?(msg)
                }
                self.receive(on: t)
            case .success:
                self.receive(on: t)
            case .failure:
                self.onStatus?(false)
                self.scheduleReconnect()
            }
        }
    }

    private func scheduleReconnect() {
        guard let url else { return }
        let delay = retryDelay
        retryDelay = min(retryDelay * 2, 30)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.connect(url) }
    }
}

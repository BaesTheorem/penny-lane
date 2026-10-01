import Foundation
import SwiftUI

/// Finds the Mac and holds the token. Order: the last URL that worked, then
/// every URL in the discovery document (the Mac publishes its tunnel URL to
/// Workers KV), then whatever the user pasted. The token rides as a Bearer
/// header on the probe and as the cookie once the web view logs in.
@MainActor
final class Connection: ObservableObject {
    @Published var baseURL: URL?
    @Published var banner: String?
    @Published var lastError: String?
    @Published var pendingScan: String?

    private let defaults = UserDefaults.standard
    var token: String? { defaults.string(forKey: "token") }
    var discovery: String? { defaults.string(forKey: "discovery") }
    var candidates: [String] { defaults.stringArray(forKey: "candidates") ?? [] }

    func pair(payload: String) {
        // Payload: pennylane://pair?d=<base64 json {token, discovery, url}> or the raw base64.
        var b64 = payload
        if let u = URL(string: payload), let q = URLComponents(url: u, resolvingAgainstBaseURL: false)?.queryItems,
           let d = q.first(where: { $0.name == "d" })?.value { b64 = d }
        guard let data = Data(base64Encoded: b64.padding(toLength: ((b64.count + 3) / 4) * 4, withPad: "=", startingAt: 0)),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            lastError = "That QR is not a Penny Lane pairing code."; return
        }
        if let t = obj["token"] as? String { defaults.set(t, forKey: "token") }
        if let d = obj["discovery"] as? String { defaults.set(d, forKey: "discovery") }
        var c = candidates
        if let u = obj["url"] as? String, !c.contains(u) { c.insert(u, at: 0) }
        defaults.set(c, forKey: "candidates")
        lastError = nil
        Task { await connect() }
    }

    func setManual(url: String) {
        var c = candidates
        if !c.contains(url) { c.insert(url, at: 0) }
        defaults.set(c, forKey: "candidates")
        Task { await connect() }
    }

    func handle(url: URL) {
        if url.host == "pair" { pair(payload: url.absoluteString) }
    }

    func connect() async {
        banner = "Finding the Mac…"
        var list = candidates
        if let d = discovery, let more = await fetchDiscovery(d) {
            for u in more where !list.contains(u) { list.append(u) }
        }
        for u in list {
            if await probe(u) {
                baseURL = URL(string: u)
                var c = candidates
                c.removeAll { $0 == u }
                c.insert(u, at: 0)
                defaults.set(c, forKey: "candidates")
                banner = nil
                return
            }
        }
        banner = list.isEmpty ? nil : "Mac unreachable. Is it awake with Penny Lane running?"
    }

    private func fetchDiscovery(_ url: String) async -> [String]? {
        guard let u = URL(string: url) else { return nil }
        var req = URLRequest(url: u); req.timeoutInterval = 8
        guard let (data, _) = try? await URLSession.shared.data(for: req),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj["urls"] as? [String]
    }

    private func probe(_ base: String) async -> Bool {
        guard let u = URL(string: base + "/remote/ping") else { return false }
        var req = URLRequest(url: u); req.timeoutInterval = 4
        if let t = token { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }
        guard let (data, resp) = try? await URLSession.shared.data(for: req),
              let http = resp as? HTTPURLResponse, http.statusCode == 200,
              let s = String(data: data, encoding: .utf8), s.contains("penny-lane") else { return false }
        return true
    }
}

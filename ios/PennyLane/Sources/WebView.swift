import SwiftUI
import WebKit

/// Renders the Mac's web UI. Logs in by POSTing the token to /remote/login
/// inside the web view (that is the only way the cookie lands in WK's jar),
/// then hands scanned barcodes to the page through window.pennyScan(code).
struct WebView: UIViewRepresentable {
    let url: URL
    let conn: Connection
    let onScanRequest: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(context.coordinator, name: "scan")
        cfg.applicationNameForUserAgent = "PennyLaneShell/1.0"
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.navigationDelegate = context.coordinator
        wv.isOpaque = false
        wv.backgroundColor = UIColor(red: 0.07, green: 0.075, blue: 0.086, alpha: 1)
        wv.scrollView.contentInsetAdjustmentBehavior = .never
        context.coordinator.webView = wv
        context.coordinator.login()
        return wv
    }

    func updateUIView(_ wv: WKWebView, context: Context) {
        if let code = conn.pendingScan {
            conn.pendingScan = nil
            let js = "window.pennyScan && window.pennyScan(\(code.debugDescription))"
            wv.evaluateJavaScript(js)
        }
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
        let parent: WebView
        weak var webView: WKWebView?
        init(_ p: WebView) { parent = p }

        func login() {
            guard let wv = webView else { return }
            if let t = parent.conn.token {
                // A form POST navigation: the 303 to "/" carries the Set-Cookie.
                let html = """
                <html><body><form id=f method=post action="\(parent.url.absoluteString)/remote/login">
                <input type=hidden name=token value="\(t)"></form><script>document.getElementById('f').submit()</script></body></html>
                """
                wv.loadHTMLString(html, baseURL: parent.url)
            } else {
                wv.load(URLRequest(url: parent.url))
            }
        }

        func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
            if m.name == "scan" { parent.onScanRequest() }
        }

        func webView(_ wv: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            parent.conn.banner = "Lost the Mac: \(error.localizedDescription)"
            Task { await parent.conn.connect() }
        }
    }
}

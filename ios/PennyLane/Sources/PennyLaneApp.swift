import SwiftUI

@main
struct PennyLaneApp: App {
    @StateObject private var conn = Connection()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(conn)
                .preferredColorScheme(.dark)
                .onOpenURL { url in conn.handle(url: url) }
        }
    }
}

struct RootView: View {
    @EnvironmentObject var conn: Connection
    @State private var showScanner = false
    @State private var showPairing = false

    var body: some View {
        ZStack {
            Color(red: 0.07, green: 0.075, blue: 0.086).ignoresSafeArea()
            if conn.baseURL == nil {
                PairingPrompt(showPairing: $showPairing)
            } else if let base = conn.baseURL {
                WebView(url: base, conn: conn, onScanRequest: { showScanner = true })
                    .ignoresSafeArea(edges: .bottom)
            }
            if let msg = conn.banner {
                VStack {
                    Text(msg).font(.footnote).padding(8)
                        .frame(maxWidth: .infinity)
                        .background(Color(red: 0.16, green: 0.16, blue: 0.18))
                        .border(Color(white: 0.27), width: 1)
                    Spacer()
                }
            }
        }
        .sheet(isPresented: $showScanner) {
            BarcodeScannerSheet { code in
                showScanner = false
                conn.pendingScan = code
            }
        }
        .sheet(isPresented: $showPairing) {
            PairingScannerSheet { payload in
                showPairing = false
                conn.pair(payload: payload)
            }
        }
        .task { await conn.connect() }
    }
}

struct PairingPrompt: View {
    @Binding var showPairing: Bool
    @EnvironmentObject var conn: Connection
    @State private var manual = ""
    var body: some View {
        VStack(spacing: 16) {
            Text("¢").font(.system(size: 96, weight: .bold)).foregroundStyle(Color(red: 1, green: 0.84, blue: 0.31))
            Text("Penny Lane").font(.title2)
            Text("Pair with the Mac: Settings → Phone access → scan the QR.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            Button("Scan pairing QR") { showPairing = true }
                .buttonStyle(.bordered)
            TextField("or paste a server URL", text: $manual)
                .textFieldStyle(.roundedBorder).autocapitalization(.none).disableAutocorrection(true)
                .onSubmit { conn.setManual(url: manual) }
            if let e = conn.lastError { Text(e).font(.caption).foregroundStyle(.red) }
        }
        .padding(24)
    }
}

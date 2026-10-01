import SwiftUI
import VisionKit

/// Native barcode scanner (VisionKit DataScannerViewController, iOS 16+).
/// Returns the first UPC-A / EAN-13 / Code 128 it sees; store shelf tags at
/// Home Depot and Lowe's are Code 128 / UPC and resolve the same way.
struct BarcodeScannerSheet: View {
    let onCode: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var manual = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    ScannerView(types: [.barcode(symbologies: [.upce, .ean8, .ean13, .code128, .code39, .qr])]) { code in
                        onCode(normalize(code))
                    }
                } else {
                    Text("Barcode scanning is not available on this device.").padding()
                }
                HStack {
                    TextField("type a code", text: $manual).keyboardType(.numberPad).textFieldStyle(.roundedBorder)
                    Button("Go") { if !manual.isEmpty { onCode(manual) } }.buttonStyle(.bordered)
                }.padding(12)
            }
            .navigationTitle("Scan")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }

    /// EAN-13 of a US UPC-A starts with 0; the server wants the 12 digits.
    private func normalize(_ s: String) -> String {
        let d = s.filter(\.isNumber)
        if d.count == 13 && d.hasPrefix("0") { return String(d.dropFirst()) }
        return d.isEmpty ? s : d
    }
}

struct PairingScannerSheet: View {
    let onPayload: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScannerView(types: [.barcode(symbologies: [.qr])]) { onPayload($0) }
                .navigationTitle("Pair")
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }
}

struct ScannerView: UIViewControllerRepresentable {
    let types: Set<DataScannerViewController.RecognizedDataType>
    let onCode: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: types, qualityLevel: .balanced,
                                           recognizesMultipleItems: false, isHighFrameRateTrackingEnabled: true,
                                           isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onCode: (String) -> Void
        private var fired = false
        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ s: DataScannerViewController, didAdd added: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !fired else { return }
            for item in added {
                if case .barcode(let b) = item, let v = b.payloadStringValue {
                    fired = true
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    s.stopScanning()
                    onCode(v)
                    return
                }
            }
        }
    }
}

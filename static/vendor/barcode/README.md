# Barcode reader (vendored)

- `barcode-detector.js`: barcode-detector 3.2.2, `dist/iife/ponyfill.js` (MIT, github.com/Sec-ant/barcode-detector)
- `zxing_reader.wasm`: zxing-wasm 3.1.3, `dist/reader/` (MIT, github.com/Sec-ant/zxing-wasm), the exact version barcode-detector 3.2.2 pins

The page loads these only when the camera opens, and only when the browser has
no native `BarcodeDetector` for retail codes. To update, `npm pack` both
packages at matching versions and copy the same two files.

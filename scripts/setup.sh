#!/usr/bin/env bash
# One-time setup: venv, deps, vendored fonts (not committed: 9 MB), config.
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
[[ -x .venv/bin/python ]] || uv venv --python 3.12 .venv
uv pip install --quiet --python .venv/bin/python flask curl_cffi segno pillow
V=web/static/vendor
LOCAL=~/Documents/material-design/fonts
for f in MaterialSymbolsRounded.woff2 Roboto.ttf; do
  [[ -f $V/$f ]] && continue
  if [[ -f $LOCAL/$f ]]; then cp "$LOCAL/$f" "$V/$f"; continue; fi
  case $f in
    MaterialSymbolsRounded.woff2) curl -fsSL -o "$V/$f" "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.woff2" ;;
    Roboto.ttf) curl -fsSL -o "$V/$f" "https://raw.githubusercontent.com/googlefonts/roboto-3-classic/main/fonts/variable/Roboto%5Bwdth%2Cwght%5D.ttf" ;;
  esac
done
[[ -f config.json ]] || { cp config.example.json config.json; echo "edit config.json: set your zip, then run bin/penny stores"; }
mkdir -p ~/Library/Logs/exobrain
echo "ok. start the server: bin/penny serve   (or install launchd/*.plist into ~/Library/LaunchAgents)"

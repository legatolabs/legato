#!/usr/bin/env bash
# Build Legato.app from this checkout: the Swift shell, plus a Python engine (this repo, installed editable).
#
#   app/macos/build.sh            # builds app/macos/build/Legato.app
#   app/macos/build.sh --open     # and opens it
#
# The engine is the repo's .venv (made here if missing, with `pip install -e ".[local]"`): edits to legato/ take
# effect the next time the app starts its engine. LEGATO_ENGINE_PYTHON points it at another Python instead.
#
# Signing: macOS ties the Accessibility and Screen Recording permissions to the app's signature. With the same
# certificate on every build they survive rebuilds; with an ad-hoc signature they have to be granted again each time.
# The first "Apple Development" or "Developer ID Application" identity in your keychain is used, or LEGATO_SIGN_ID.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/build/Legato.app"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml")"

# ---- engine
PY="${LEGATO_ENGINE_PYTHON:-$REPO/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  echo "==> making the engine venv at $REPO/.venv"
  python3 -m venv "$REPO/.venv"
  PY="$REPO/.venv/bin/python"
fi
if ! "$PY" -c "import legato.app_engine, pyautogui, Quartz" 2>/dev/null; then
  echo "==> installing legato[local] into $PY"
  "$PY" -m pip install -q -e "$REPO[local]"
fi

# ---- Swift
echo "==> swift build"
(cd "$HERE" && swift build -c release --arch arm64)
BIN="$(cd "$HERE" && swift build -c release --arch arm64 --show-bin-path)/Legato"

# ---- bundle
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
cp "$BIN" "$OUT/Contents/MacOS/Legato"
sed -e "s|@VERSION@|$VERSION|" -e "s|@BUILD@|$(date +%Y%m%d%H%M)|" -e "s|@ENGINE_PYTHON@|$PY|" \
  "$HERE/Info.plist.in" > "$OUT/Contents/Info.plist"

ICONSET="$HERE/build/AppIcon.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/Resources/AppIcon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$HERE/Resources/AppIcon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$OUT/Contents/Resources/AppIcon.icns"

# ---- sign
ID="${LEGATO_SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null \
  | sed -nE 's/.*"((Developer ID Application|Apple Development): [^"]*)".*/\1/p' | head -1)}"
if [ -n "$ID" ]; then
  echo "==> signing with $ID"
  codesign --force --deep --sign "$ID" "$OUT"
else
  echo "==> no signing identity: ad-hoc (permissions will need granting again after each build)"
  codesign --force --deep --sign - "$OUT"
fi

echo "==> $OUT"
if [ "${1:-}" = "--open" ]; then
  pkill -x Legato 2>/dev/null || true
  sleep 0.5
  open "$OUT"
fi

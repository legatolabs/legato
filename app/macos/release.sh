#!/usr/bin/env bash
# A Legato.app for other Macs, in a .dmg: the Swift app with its own Python engine inside (a standalone CPython from
# astral-sh/python-build-standalone, plus legato[local] installed from this checkout), signed with a Developer ID
# certificate (hardened runtime), notarized by Apple and stapled, so it opens on any Mac without warnings.
#
#   app/macos/release.sh                 # app/macos/dist/Legato-<version>.dmg, notarized
#   NOTARIZE=0 app/macos/release.sh      # signed, not notarized (a quick check that it builds and runs)
#
# Needs: Xcode command line tools, a "Developer ID Application" identity in the keychain (LEGATO_SIGN_ID to pick one),
# and notarytool credentials saved as a keychain profile (LEGATO_NOTARY_PROFILE, default "legato-notary"):
#   xcrun notarytool store-credentials legato-notary --key AuthKey_X.p8 --key-id X --issuer Y
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DIST="$HERE/dist"
APP="$DIST/Legato.app"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml")"
DMG="$DIST/Legato-$VERSION.dmg"
PROFILE="${LEGATO_NOTARY_PROFILE:-legato-notary}"
# the engine's Python: a release of python-build-standalone, checked against its published SHA256SUMS
PBS_TAG="20261110"
PBS_FILE="cpython-3.13.18+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}"
CACHE="$HOME/Library/Caches/legato-release"
# what the engine imports on macOS beyond legato[local]: screenshots and input (Quartz), reading text (Vision),
# Accessibility (ApplicationServices)
EXTRA_PKGS=(pyobjc-framework-Quartz pyobjc-framework-Vision pyobjc-framework-ApplicationServices)

ID="${LEGATO_SIGN_ID:-$(security find-identity -v -p codesigning 2>/dev/null \
  | sed -nE 's/.*"(Developer ID Application: [^"]*)".*/\1/p' | head -1)}"
[ -n "$ID" ] || { echo "no Developer ID Application identity in the keychain (LEGATO_SIGN_ID)"; exit 1; }
echo "==> Legato $VERSION, signing as: $ID"

rm -rf "$DIST" && mkdir -p "$DIST" "$CACHE"

# ---- Swift app
echo "==> swift build"
(cd "$HERE" && swift build -c release --arch arm64)
BIN="$(cd "$HERE" && swift build -c release --arch arm64 --show-bin-path)/Legato"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Legato"
sed -e "s|@VERSION@|$VERSION|" -e "s|@BUILD@|$(date +%Y%m%d%H%M)|" -e "s|@ENGINE_PYTHON@||" \
  "$HERE/Info.plist.in" > "$APP/Contents/Info.plist"
ICONSET="$DIST/AppIcon.iconset"
mkdir -p "$ICONSET"
for s in 16 32 128 256 512; do
  sips -z $s $s "$HERE/Resources/AppIcon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$HERE/Resources/AppIcon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONSET"

# ---- engine: standalone Python + legato
echo "==> engine: $PBS_FILE"
[ -f "$CACHE/$PBS_FILE" ] || curl -fsSL -o "$CACHE/$PBS_FILE" "$PBS_URL/$PBS_FILE"
curl -fsSL -o "$CACHE/SHA256SUMS-$PBS_TAG" "$PBS_URL/SHA256SUMS"
WANT="$(grep " $PBS_FILE\$" "$CACHE/SHA256SUMS-$PBS_TAG" | cut -d' ' -f1)"
GOT="$(shasum -a 256 "$CACHE/$PBS_FILE" | cut -d' ' -f1)"
[ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || { echo "checksum mismatch for $PBS_FILE"; rm -f "$CACHE/$PBS_FILE"; exit 1; }
ENGINE="$APP/Contents/Resources/engine"
mkdir -p "$ENGINE"
tar -xzf "$CACHE/$PBS_FILE" -C "$ENGINE" --strip-components 1          # the archive's top folder is python/
PY="$ENGINE/bin/python3"
PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONNOUSERSITE=1 "$PY" -m pip install -q --no-cache-dir --no-warn-script-location \
  "$REPO[local]" "${EXTRA_PKGS[@]}"
"$PY" -c "import legato.app_engine, legato.axwait, pyautogui, Quartz, Vision, ApplicationServices, PIL, httpx; print('engine imports ok')"
# leave out what the engine never uses (a terminal UI, tests, pip itself), then compile every module so Python never
# writes into the signed bundle
LIB="$(echo "$ENGINE"/lib/python3.*)"
rm -rf "$LIB"/{idlelib,tkinter,turtledemo,test,ensurepip} "$LIB"/lib-dynload/_tkinter* "$ENGINE"/lib/{tcl,tk,itcl,thread}* \
  "$ENGINE"/share "$ENGINE"/include
find "$ENGINE" -type d \( -name tests -o -name test \) -path "*/site-packages/*" -prune -exec rm -rf {} +
"$PY" -m pip uninstall -q -y pip >/dev/null 2>&1 || true
find "$ENGINE" -name "__pycache__" -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q -j 0 "$LIB" >/dev/null || true
du -sh "$ENGINE" | sed 's/^/==> engine size: /'

# ---- sign, inside out: every Mach-O in the engine, the engine's python with its entitlements, then the app
echo "==> signing"
sign() { codesign --force --timestamp --options runtime --sign "$ID" "$@"; }
find "$ENGINE" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 | while IFS= read -r -d '' f; do sign "$f"; done
find "$ENGINE/bin" -type f -perm -u+x -print0 | while IFS= read -r -d '' f; do
  if file "$f" | grep -q "Mach-O"; then sign --entitlements "$HERE/engine.entitlements" "$f"; fi
done
sign --entitlements "$HERE/app.entitlements" "$APP"
codesign --verify --deep --strict --verbose=1 "$APP"

# ---- dmg: the app and a link to /Applications
echo "==> dmg"
STAGE="$DIST/dmg"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Legato" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
codesign --force --timestamp --sign "$ID" "$DMG"

if [ "${NOTARIZE:-1}" = "1" ]; then
  echo "==> notarizing (a few minutes)"
  xcrun notarytool submit "$DMG" --keychain-profile "$PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler staple "$APP"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
  spctl --assess --type execute --verbose=2 "$APP"
fi
ls -lh "$DMG" | awk '{print "==> " $NF ", " $5}'

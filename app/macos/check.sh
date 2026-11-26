#!/usr/bin/env bash
# The checks behind app/macos/TESTING.md: what the test copy of Legato for Mac saved, from its settings, Keychain,
# run records and engine log. Each prints what it found and exits 1 when it is not what the case expects.
#
#   app/macos/check.sh build              build ~/Applications/Legato Test.app (its own bundle id, settings and key)
#   app/macos/check.sh start              note the time (later checks only look at runs after it) and open the app
#   app/macos/check.sh onboarded          onboarding was finished
#   app/macos/check.sh model              the provider and model in Settings
#   app/macos/check.sh keychain           the Keychain items: one for every key, none of the old one-per-provider
#   app/macos/check.sh run [STATUS]       the newest run since start, and that it ended STATUS (done, stopped, ...)
#   app/macos/check.sh history            every run since start has its run.json and a screenshot per turn
#   app/macos/check.sh log [WORD]         the engine log since start: no traceback (or that it has WORD)
#   app/macos/check.sh alive              the app is still running
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ID="${LEGATO_TEST_BUNDLE_ID:-computer.legato.mac.test}"
APP="$HOME/Applications/Legato Test.app"
RUNS="$HOME/Library/Application Support/Legato/runs"
LOG="$HOME/Library/Logs/Legato/engine.log"
MARK="${TMPDIR:-/tmp}/legato-test.start"
fail() { echo "FAIL: $*"; exit 1; }
since() { [ -f "$MARK" ] && cat "$MARK" || echo 0; }

case "${1:-}" in
build)
  "$HERE/build.sh"
  B="$HERE/build/Legato.app"
  /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $ID" -c "Set :CFBundleName 'Legato Test'" \
    -c "Set :CFBundleDisplayName 'Legato Test'" "$B/Contents/Info.plist"
  # sign again as build.sh did (its certificate, or ad-hoc), so a certificate's permissions survive rebuilds
  sign=$(codesign -dvv "$B" 2>&1 | sed -n 's/^Authority=//p' | head -1)
  codesign --force --deep --sign "${LEGATO_SIGN_ID:-${sign:--}}" "$B" 2>/dev/null
  was=$(defaults read "$APP/Contents/Info" CFBundleIdentifier 2>/dev/null || true)
  mkdir -p "$HOME/Applications" && rm -rf "$APP" && ditto "$B" "$APP"
  if [ -n "$was" ] && [ "$was" != "$ID" ]; then
    # macOS's Screen Recording prompt remembers the id it saw at this path ($was) and would save the grant for it
    killall universalAccessAuthWarn 2>/dev/null || true
    echo "note: this path held $was; restarted macOS's Screen Recording prompt so it reads $ID"
  fi
  echo "built $APP ($ID, signed ${LEGATO_SIGN_ID:-${sign:-ad-hoc}})" ;;
start)
  date +%s > "$MARK"; (stat -f %z "$LOG" 2>/dev/null || echo 0) > "$MARK.log"
  pgrep -x Legato >/dev/null && echo "note: another Legato is running; quit it first (they share Fn and ⌥⇧Space)"
  open "$APP"; echo "started; checks look at runs after $(date -r "$(since)" +%T)" ;;
onboarded)
  v=$(defaults read "$ID" onboarded 2>/dev/null || echo 0); echo "onboarded=$v"; [ "$v" = 1 ] || fail "not onboarded" ;;
model)
  p=$(defaults read "$ID" providerID 2>/dev/null || echo "(legato setup's)"); m=$(defaults read "$ID" model 2>/dev/null || echo "(legato setup's)")
  echo "provider=$p model=${m:-(none)}" ;;
keychain)
  svc="$ID.api-key"
  security find-generic-password -s "$svc" -a keys >/dev/null 2>&1 && echo "one item for every key: present" || echo "one item: none yet"
  old=0
  for p in openai anthropic gemini xai openrouter together fireworks groq cerebras custom; do
    security find-generic-password -s "$svc" -a "$p" >/dev/null 2>&1 && { echo "old item left: $p"; old=$((old+1)); }
  done
  [ "$old" = 0 ] || fail "$old old one-per-provider items" ;;
run)
  want="${2:-}"; s=$(since)
  d=$(ls -dt "$RUNS"/*/ 2>/dev/null | while read -r r; do if [ "$(stat -f %m "$r")" -ge "$s" ]; then echo "$r"; fi; done | head -1 || true)
  [ -n "$d" ] || fail "no run since start"
  python3 - "$d/run.json" "$want" <<'EOF'
import json, sys
j = json.load(open(sys.argv[1]))
u = j.get("usage") or {}
print(f"{j.get('id')}: {j.get('status')} in {j.get('steps')} steps, {j.get('wall_s', 0):.0f} s, ${u.get('cost_usd', 0):.3f}")
print("  task:", j.get("task")); print("  said:", " ".join((j.get("message") or j.get("error") or "").split())[:200])
if sys.argv[2] and j.get("status") != sys.argv[2]:
    print(f"FAIL: ended {j.get('status')}, expected {sys.argv[2]}"); sys.exit(1)
EOF
  ;;
history)
  s=$(since); n=0; bad=0
  for r in "$RUNS"/*/; do
    [ "$(stat -f %m "$r")" -ge "$s" ] || continue; n=$((n+1))
    python3 - "$r" <<'EOF' || bad=$((bad+1))
import json, os, sys
d = sys.argv[1]; j = json.load(open(os.path.join(d, "run.json")))
missing = [t.get("shot") for t in j.get("turns", []) if t.get("shot") and not os.path.exists(os.path.join(d, t["shot"]))]
print(f"{j.get('id')}: {j.get('status')}, {len(j.get('turns', []))} turns" + (f", missing {missing}" if missing else ""))
sys.exit(1 if missing else 0)
EOF
  done
  echo "$n runs since start"; [ "$bad" = 0 ] || fail "$bad runs with missing screenshots" ;;
log)
  word="${2:-}"
  # what the engine wrote since start (the log's size then is in $MARK.log)
  tail -c +"$(( $(cat "$MARK.log" 2>/dev/null || echo 0) + 1 ))" "$LOG" > "${TMPDIR:-/tmp}/legato-test.log"
  if [ -n "$word" ]; then grep -i -- "$word" "${TMPDIR:-/tmp}/legato-test.log" | tail -5 || fail "no \"$word\" in the log"
  else ! grep -n "Traceback" "${TMPDIR:-/tmp}/legato-test.log" | tail -3 || fail "a traceback in the engine log"; echo "no traceback"; fi ;;
alive)
  pgrep -f "Legato Test.app/Contents/MacOS" >/dev/null && echo "running" || fail "Legato Test is not running (crashed?)" ;;
*)
  sed -n '2,16p' "$0"; exit 2 ;;
esac

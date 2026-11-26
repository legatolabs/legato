# Legato for Mac: the Screen Recording step in onboarding doesn't work for a self-built copy

## The problem

The new onboarding (branch `mac-onboarding`, on top of PR #110) asks for each permission one step at a time.
Accessibility and Input Monitoring work: macOS asks, the app shows up in System Settings, the person switches it on,
and the step moves on by itself. **Screen Recording does not:** after "Allow…", the app ("Legato Test") never appears in
System Settings → Privacy & Security → Screen & System Audio Recording, so there's nothing to switch on and the step
can't finish.

## Setup

- macOS 26, Apple silicon Mac.
- A test copy is built with `app/macos/check.sh build` (with `LEGATO_TEST_BUNDLE_ID=computer.legato.mac.firstrun`):
  `build.sh`, then PlistBuddy sets the bundle id and the name "Legato Test", then an **ad-hoc** `codesign --force --deep
  --sign -`, then a copy to `~/Applications/Legato Test.app`.
- The installed release `/Applications/Legato.app` (Developer ID, id `computer.legato.mac`) **is** in
  the Screen Recording list and works. The two `Info.plist`s are the same apart from id, name and engine path; neither
  has `NSScreenCaptureUsageDescription`.
- Each rebuild gets a new ad-hoc signature (cdhash), so earlier grants are lost. Between runs:
  `tccutil reset {ScreenCapture,Accessibility,ListenEvent} computer.legato.mac.firstrun` and
  `defaults delete computer.legato.mac.firstrun`.

## What the code does on the screen step

`app/macos/Sources/Legato/Permissions.swift`, `Permission.request()`:

1. `SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)` in a Task (added because
   `CGRequestScreenCaptureAccess()` alone didn't seem to add the app to the list).
2. The first click calls `CGRequestScreenCaptureAccess()`; later clicks open
   `x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture` (System Settings is quit first, so the
   list reloads).
3. `PermissionState` polls `CGPreflightScreenCaptureAccess()` every second; the step moves on when it's true, then
   offers "Reopen Legato" (Screen Recording takes effect after a relaunch).

## What's been found

- **macOS holds a deny.** The app's headless check (`open -n -g ~/Applications/"Legato Test.app" --args --capture-check
  out.json`, which uses `SCScreenshotManager`) fails with **"The user declined TCCs for application, window, display
  capture"**.
- **Right after `tccutil reset ScreenCapture computer.legato.mac.firstrun`, the prompt does appear**: "“Legato Test”
  would like to record this computer's screen and audio" (Open System Settings / Deny). So the request reaches macOS.
- **But the app still isn't in the list** after "Open System Settings", even with System Settings quit and reopened. Its
  icon can be dragged in, or + used, but the person reported still being stuck. Whether + adds it is unconfirmed.
- LaunchServices has exactly one registration for `computer.legato.mac.firstrun`: `~/Applications/Legato Test.app`.
  An old copy under another id (`computer.legato.mac.itest`, in a temp build folder) is also registered.
- The TCC database and the `tccd` log aren't readable from the shell used (no Full Disk Access), so what record macOS
  stores is unknown.

## Leading hypothesis (unverified)

On macOS 26, the Screen & System Audio Recording list doesn't show ad-hoc–signed apps (no team id), or it hides an entry
whose stored code requirement (an ad-hoc cdhash) no longer matches. The Accessibility and Input Monitoring lists do
show the same app. If that's right, a Developer ID–signed build works, and only self-built copies hit this, which also
affects contributors building the app (issue #89).

## What's needed

1. Confirm or rule out the hypothesis. For example: sign the test copy with a real Apple Development or Developer ID
   identity (or a self-signed certificate in the login keychain) and see whether it appears in the list. Read the TCC
   record with Full Disk Access:
   `sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db "select service,client,auth_value,auth_reason,csreq from access where client like 'computer.legato%'"`.
2. Make the screen step work for anyone who builds the app themselves, or detect the case and tell them exactly what to
   do. Options: sign `check.sh build` and `build.sh` with a stable identity when one exists; a stable self-signed
   identity, so grants survive rebuilds; or onboarding text for this case.
3. Don't change the Developer ID release flow (`release.sh`), and don't touch the installed `/Applications/Legato.app`
   or its permissions.

## Files

- `app/macos/Sources/Legato/Permissions.swift`: `Permission`, `PermissionState`, the stepped `OnboardingView`.
- `app/macos/Sources/Legato/AppController.swift`: `showOnboarding`, `finishOnboarding`, the window delegate.
- `app/macos/Sources/Legato/CaptureCheck.swift`, `Capture.swift`: ScreenCaptureKit use.
- `app/macos/check.sh`, `app/macos/build.sh`: the build and signing above. `app/macos/TESTING.md`: cases A1–A7.

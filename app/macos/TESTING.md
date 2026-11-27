# Testing Legato for Mac by hand

What to run before a release: a new user's first launch through to a finished task. The steps marked **check** are
verified by `app/macos/check.sh CASE`, which reads the app's settings, Keychain, run records and engine log, so a
result does not depend on what someone remembers seeing.

## Before you start

1. Build a test copy that cannot touch an installed Legato (its own bundle id, settings and Keychain item; the
   Keychain item follows the bundle id since #94):
   ```bash
   app/macos/check.sh build        # ~/Applications/Legato Test.app
   ```
   With no Apple Development certificate it is signed ad hoc, so each build needs its permissions again;
   `app/macos/build.sh --make-signing-id` (once) makes a local certificate that keeps them.
2. Quit any other Legato (they share Fn and ⌥⇧Space), then `app/macos/check.sh start` to note the time and open it.
3. A model key: the one `legato setup` saved is used (the app starts from it). Each task stops at $1.

Two things that are not bugs in the test: Legato's HUD and command bar are left out of screenshots on purpose
(`sharingType = .none`), so a screen recording of a test shows neither; and on a full menu bar the Legato icon can sit
behind the notch (#97), where System Events can still open its menu.

Every case lists what to do, what should happen, and how it is checked. Write PASS or FAIL with a note; file each FAIL
under the Mac app epic (#93) with the case id.

## A. First launch

| Id | Do | Should happen | Check |
|---|---|---|---|
| A1 | Open Legato Test | "Set up Legato" opens; the menu bar shows the Legato icon | look |
| A2 | Allow… for each permission, switch it on in System Settings | Each row turns green within about 1 s | look |
| A3 | Reopen when asked (Screen Recording) | The app comes back; all three rows green; the menu says "Permissions…" with no "(N missing)" | look |
| A4 | Read the model line | `legato setup`'s model and provider, ready (green) | look |
| A5 | "Open Keyboard Settings" | Keyboard settings open | look |
| A6 | Done | The window closes and stays closed on the next launch | `check.sh onboarded` |
| A7 | Close the window with the red button instead (fresh copy) | Treated as Later (#85) | `check.sh onboarded` |

## B. Settings and keys

| Id | Do | Should happen | Check |
|---|---|---|---|
| B1 | Settings → Provider: OpenAI | Model empty; "Choose a model" (#82) | `check.sh model` |
| B2 | Provider back to OpenRouter | Its model comes back | `check.sh model` |
| B3 | Paste a key for a second provider, then quit and reopen | At most one Keychain prompt in all, none after Always Allow (#81) | `check.sh keychain` |
| B4 | Test connection | "Answered in N s and reads images." | look |
| B5 | Refresh models (↻) | "N models from …" and the ▾ menu lists them | look |

## C. Tasks

| Id | Do | Should happen | Check |
|---|---|---|---|
| C1 | Tap Fn (or ⌥⇧Space), type `Open TextEdit, make a new document and type hello from Legato`, Return | The pill shows steps, the ghost cursor names targets, TextEdit has the text, the run ends done | `check.sh run done` |
| C2 | Same as C1 with ⌥⇧Space instead of Fn | The command bar opens | look |
| C3 | Hold Fn, say "what time is it in Tokyo", let go | The words appear as you speak; the answer comes back | `check.sh run done` |
| C4 | Hold Fn on a Mac with no mic, or with another app holding it | An error in the pill, no crash (#88) | `check.sh alive` |
| C5 | A follow-up within 10 min: "now make it bold" | Works on the same document | `check.sh run done` |
| C6 | History → Run again on C1 | Starts and finishes; "Reusing N steps" shows | `check.sh run done` |

## D. Control

| Id | Do | Should happen | Check |
|---|---|---|---|
| D1 | Start a long task, move the mouse | It pauses: "You moved the mouse"; the menu shows Resume | `check.sh run` (a turn reads `paused`) |
| D2 | Resume | It looks at the screen again, then goes on | `check.sh run done` |
| D3 | Start a task, press Esc | Stops within 5 s | `check.sh run stopped` |
| D4 | `Empty the Trash` | Asks first; Deny ends the step without doing it | `check.sh run` |
| D5 | Lock the screen mid-run | Paused; nothing clicked while locked | `check.sh run` (a turn reads `paused`) |
| D6 | Set the cost cap to $0.01, run C1 | Ends with the cost cap | `check.sh run cost_cap` |

## E. Afterwards

| Id | Do | Should happen | Check |
|---|---|---|---|
| E1 | History | Every run above, with a screenshot per step, time and cost | `check.sh history` |
| E2 | Quit and reopen | No onboarding, no Keychain prompt, the last model kept | `check.sh keychain` |
| E3 | The engine log | No traceback | `check.sh log` |

## F. Release build (once per release)

| Id | Do | Should happen | Check |
|---|---|---|---|
| F1 | Download `Legato.dmg` from the latest release, drag to Applications, open | Opens without a Gatekeeper warning | `spctl -a -vv /Applications/Legato.app` |
| F2 | Check for Updates… on the previous version | Offers this version and installs it | look |

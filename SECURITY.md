# Security

Legato controls a computer, so we treat a way around its limits as a security bug.

## Report a vulnerability

Report it privately: on GitHub, open the repo's **Security** tab and choose **Report a vulnerability**. Please don't
open a public issue. We aim to reply within a few days, and tell you when a fix ships. Credit is yours to take or
decline.

Only the latest release gets fixes (0.3.x today; `pip install -U legatolabs`).

## In scope

- A risky key or command (`rm`, `sudo`, Cmd+Q...) that runs without a yes, when the README says it waits.
- The MCP server acting on your own screen without `--yes`, or reaching beyond the desktop it was given.
- Background mode on a Mac changing a window or document that isn't its own.
- An API key, screenshot or task sent anywhere but the model you chose, or written to a log in plain text.
- Code running on the host from inside `--sandbox docker`.
- The Mac app's signed `.dmg` or its update feed accepting something it shouldn't.

## Known limits, not bugs

These are in the README's [Safety and limits](README.md#safety-and-limits): the risky list is a list, not a
sandbox (a click on Delete isn't caught), and text on screen can try to instruct the model (prompt injection).
A report that makes either of them worse than documented is welcome.

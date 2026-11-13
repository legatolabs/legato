# Playwright MCP or a desktop: how your coding agent should test your app

Both let Claude Code, Codex or Cursor check the app you are building. [Playwright
MCP](https://github.com/microsoft/playwright-mcp) drives a browser through its accessibility tree. Legato gives the
agent a whole desktop, which it sees through screenshots. Checked against Playwright MCP's README on 13 November 2026.

| | Playwright MCP | Legato |
|---|---|---|
| drives | a browser: Chrome, Edge, Firefox or WebKit | a desktop and any app on it, browser included |
| where | your machine (headed by default, `--headless` optional) | a Docker container on your machine, your own screen, any sandbox you can run a command in, or E2B |
| the model sees | an accessibility snapshot by default; a screenshot when it asks; coordinate tools with `--caps=vision` | a screenshot after every action; pixel coordinates |
| finds things by | element references from the snapshot | where they are on the screen |
| install | `claude mcp add playwright npx @playwright/mcp@latest` | `claude mcp add legato -- legato mcp --sandbox docker` |
| license | Apache-2.0 | Apache-2.0 |

## Use Playwright MCP when

- The app is a web page and the accessibility tree describes what you are checking: text, links, forms, buttons.
- You want element references instead of coordinates, or plan to turn the check into a Playwright test.

## Use Legato when

- **What you are checking is visual.** A chart on a canvas, WebGL, a layout or a colour is not in the accessibility
  tree. Legato returns a screenshot after every action, so the agent looks at the page by default. On the
  [home page](https://legato.computer), Claude Code spots bars drawn upside down on a canvas, fixes one line and
  reloads to check the fix.
- **It is not a web page.** An Electron, Tauri, Qt or GTK app, or anything else outside a browser
  ([guide](../guides/desktop-apps.md)).
- **The flow leaves the page.** A system file picker or print dialog, a browser extension, or a second app.
- **You want it off your machine.** `--sandbox docker` is a throwaway Linux desktop that needs only Docker, and your
  dev server is reachable from it at `host.docker.internal` ([guide](../guides/coding-agent-computer.md)). Playwright
  MCP's `--isolated` keeps the browser profile in memory, but the browser still runs on your machine.
- **A cheaper or local model should do the clicking.** `desktop_run_task` hands a task to any OpenAI-compatible
  model ([models](../models.md)).

You can add both servers. The agent can then use Playwright for the DOM and Legato for everything else.

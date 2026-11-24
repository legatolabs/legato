# Windows desktop apps: Legato and Devin (23 Nov 2026)

Raw data for [Windows desktop apps](../../../docs/benchmarks.md#windows-desktop-apps-legato-and-devin) (legato-research
EXP-0094).

| file | what |
|---|---|
| `tasks.json` | the six basic tasks, each with its PowerShell check |
| `invoice-desk/` | the invoice task: `setup.ps1` (builds the Invoice Desk program and `invoices.txt`), `check.ps1` (reads the saved rows), `tasks.json` |
| `legato-smoke.jsonl` | Legato's first run on the machine (Notepad) |
| `legato-six-tasks.jsonl` | Legato, the six tasks, two runs each (`rep` 0 and 1), one after another |
| `legato-invoice-desk.jsonl` | Legato, every invoice run; `valid: false` marks the one that began without a reset stage |
| `devin-six-tasks-cold.json` | Devin, the six tasks, each in a new session (machine start-up included) |
| `devin-six-tasks-warm.json` | Devin, the six tasks, each sent to an already running session |
| `devin-invoice-desk.json` | Devin, the invoice task (no video) |
| `devin-invoice-desk-recorded.json` | Devin, the invoice task with its own screen recording |

- **Legato:** `wall_s` is from the first screenshot to done. GPT-6.1 Terra plans and UI-TARS-1.5-7B points, through
  OpenRouter, on one EC2 Windows Server 2022 machine (m7i.xlarge) at 1024x768.
- **Devin:** `seconds` is from sending the task to Devin's DONE reply (`sent_at` to `done_at`), on Devin's own Windows
  machines at 1600x1200.
- Removed for privacy: Devin's session IDs and URLs and the recording's attachment link. The Legato rows hold no
  hostnames or addresses.

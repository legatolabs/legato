# Windows desktop apps (23 Nov 2026)

Raw data for [Windows desktop apps](../../../docs/benchmarks.md#windows-desktop-apps).

| file | what |
|---|---|
| `tasks.json` | the six basic tasks, each with its PowerShell check |
| `invoice-desk/` | the invoice task: `setup.ps1` (builds the Invoice Desk program and `invoices.txt`), `check.ps1` (reads the saved rows), `tasks.json` |
| `legato-smoke.jsonl` | Legato's first run on the machine (Notepad) |
| `legato-six-tasks.jsonl` | Legato, the six tasks, two runs each (`rep` 0 and 1), one after another |
| `legato-invoice-desk.jsonl` | Legato, every invoice run; `valid: false` marks the one that began without a reset stage |

- `wall_s` is from the first screenshot to done. GPT-6.1 Terra plans and UI-TARS-1.5-7B points, through
  OpenRouter, on one EC2 Windows Server 2022 machine (m7i.xlarge) at 1024x768.
- The rows hold no hostnames or addresses.

# A DCF in Excel (demo, 23 Nov 2026)

Raw data for [A DCF in Excel (demo)](../../../docs/benchmarks.md#a-dcf-in-excel-demo) (legato-research EXP-0098).

| file | what |
|---|---|
| `takes.json` | every recorded take: seconds, steps, cells right of 81, cost, and which prompt it used |
| `t42.json` | the take in the video |
| `dcfw.py` | the workbook (`make`), the check that reads every formula and value back from Excel (`live`), and the final prompt (`TASK`) |

- GPT-6.2 Sol (effort low) in Legato's Mac setup: one plan per turn, with AppleScript and a shell beside the screen.
- `excel_s` is the run's wall time, from handing Legato the task to done. The game in the video is a separate small model, not Legato.
- `prompt: "earlier"` takes used versions of the prompt before `dcfw.py`'s current one, which spells out every formula
  and asks for one AppleScript that sets them all. The earlier versions were not kept.

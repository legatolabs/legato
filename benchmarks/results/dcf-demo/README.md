# A DCF in Excel (demo, 23 Nov 2026)

Raw data for [A DCF in Excel (demo)](../../../docs/benchmarks.md#a-dcf-in-excel-demo).

| file | what |
|---|---|
| `takes.json` | every recorded take: seconds, steps, cells right of 81, cost, and which prompt it used |
| `t42.json` | the take in the video |
| `dcfw.py` | the workbook (`make`), the check that reads every formula and value back from Excel (`live`), and the final prompt (`TASK`) |
| `nofx-takes.jsonl` | 24 Nov: takes with the formulas taken out of the prompt (`kind: nofx`, the prompt below) and with `dcfw.py`'s prompt (`kind: fx`), same session, same check |

- GPT-6.2 Sol (effort low) in Legato's Mac setup: one plan per turn, with AppleScript and a shell beside the screen.
- `excel_s` is the run's wall time, from handing Legato the task to done. The game in the video is a separate small model, not Legato.
- `prompt: "earlier"` takes used versions of the prompt before `dcfw.py`'s current one, which spells out every formula
  and asks for one AppleScript that sets them all. The earlier versions were not kept.

## Without the formulas

The same workbook, harness, model and check, with this prompt (`kind: nofx`): it says where each block goes and that it
is a standard unlevered DCF, and gives no formula. The first run is the smoke take.

```text
Build the DCF in the open Excel workbook DCF.xlsx (sheet DCF) as fast as you can: speed matters most. Script Excel itself with AppleScript (tell application "Microsoft Excel" ... set formula of range ...). Never edit the .xlsx file with another program. Every output must be an Excel formula that refers to the sheet's inputs, never a typed number.
The assumptions are in B4:B13 with their labels in A4:A13. Fill the projections F5:J14 for 2026E to 2030E (the row labels are in D5:D14, the year number 1 to 5 is in row 4, and 2025A revenue is in E5), the valuation B16:B21 (labels in A16:A21), and the sensitivity block F18:J22: value per share for each WACC in F17:J17 and each terminal growth in E18:E22. It is a standard unlevered DCF: cash flows discounted at the end of each year at the WACC, and a Gordon-growth terminal value on the last year's free cash flow.
Leave the workbook open; do not save it.
```

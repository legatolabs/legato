"""The Excel side of the stacked demo: a five-year DCF laid out wide, so it reads in the top half of a square frame.

    python dcfw.py make [PATH] | live | task
Fictional company; $m except per share. Assumptions A3:B13, projections D3:J14, valuation A16:B21, sensitivity D16:J22."""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IN = {"B4": ("Revenue growth", 0.12), "B5": ("EBITDA margin", 0.28), "B6": ("D&A (% of revenue)", 0.04),
      "B7": ("Capex (% of revenue)", 0.05), "B8": ("Increase in NWC (% of rev. growth)", 0.10), "B9": ("Tax rate", 0.25),
      "B10": ("WACC", 0.09), "B11": ("Terminal growth", 0.025), "B12": ("Net debt ($m)", 850.0),
      "B13": ("Diluted shares (m)", 120.0)}
BASE = 1200.0                      # E5: revenue in 2025A
ROWS = ["Revenue", "EBITDA", "D&A", "EBIT", "Taxes on EBIT", "Capex", "Increase in NWC", "Unlevered free cash flow",
        "Discount factor", "PV of free cash flow"]          # rows 5-14, labels in D
VAL = ["Sum of PV of free cash flows", "Terminal value", "PV of terminal value", "Enterprise value", "Equity value",
       "Value per share ($)"]                                # A16:B21
COLS = "FGHIJ"
S_WACC = [0.07, 0.08, 0.09, 0.10, 0.11]        # F17:J17
S_G = [0.015, 0.020, 0.025, 0.030, 0.035]      # E18:E22


def expected() -> dict:
    v = {k: x for k, (_, x) in IN.items()}
    out, prev, pvs, fcfs = {}, BASE, [], []
    fcf = df = 0.0
    for i, c in enumerate(COLS, 1):
        rev = prev * (1 + v["B4"])
        ebitda, da, capex, nwc = rev * v["B5"], rev * v["B6"], rev * v["B7"], (rev - prev) * v["B8"]
        ebit = ebitda - da
        tax = ebit * v["B9"]
        fcf = ebit - tax + da - capex - nwc
        df = 1 / (1 + v["B10"]) ** i
        for r, x in zip(range(5, 15), (rev, ebitda, da, ebit, tax, capex, nwc, fcf, df, fcf * df)):
            out[f"{c}{r}"] = x
        pvs.append(fcf * df)
        fcfs.append(fcf)
        prev = rev
    tv = fcf * (1 + v["B11"]) / (v["B10"] - v["B11"])
    ev = sum(pvs) + tv * df
    eq = ev - v["B12"]
    out.update(B16=sum(pvs), B17=tv, B18=tv * df, B19=ev, B20=eq, B21=eq / v["B13"])
    for i, g in enumerate(S_G):
        for j, w in enumerate(S_WACC):
            pv = sum(f / (1 + w) ** (k + 1) for k, f in enumerate(fcfs))
            out[f"{COLS[j]}{18 + i}"] = (pv + fcfs[-1] * (1 + g) / (w - g) / (1 + w) ** 5 - v["B12"]) / v["B13"]
    return out


def make(path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    wb = Workbook()
    ws = wb.active
    ws.title = "DCF"
    navy, blue, white = PatternFill("solid", fgColor="1F3864"), Font(color="0000FF"), Font(bold=True, color="FFFFFF")
    ws["A1"] = "Atlas Ledger: discounted cash flow ($m, fictional company)"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A3"] = "Assumptions"
    ws["A3"].font, ws["A3"].fill, ws["B3"].fill = white, navy, navy
    for ref, (lab, x) in IN.items():
        ws["A" + ref[1:]] = lab
        ws[ref] = x
        ws[ref].font = blue
        ws[ref].number_format = "0.0%" if x < 1 else "#,##0"
    for col, h in zip("DEFGHIJ", ("($m)", "2025A", "2026E", "2027E", "2028E", "2029E", "2030E")):
        c = ws[f"{col}3"]
        c.value, c.font, c.fill, c.alignment = h, white, navy, Alignment(horizontal="center")
    ws["D4"] = "Year"
    for i, col in enumerate(COLS, 1):
        ws[f"{col}4"] = i
        ws[f"{col}4"].alignment = Alignment(horizontal="center")
    for r, lab in zip(range(5, 15), ROWS):
        ws[f"D{r}"] = lab
        for col in COLS:
            ws[f"{col}{r}"].number_format = "0.000" if r == 13 else "#,##0.0"
    ws["E5"] = BASE
    ws["E5"].font = blue
    ws["E5"].number_format = "#,##0.0"
    for r in (12, 14):
        ws[f"D{r}"].font = Font(bold=True)
        for col in "DEFGHIJ":
            ws[f"{col}{r}"].border = Border(top=Side(style="thin"))
    ws["A15"] = "Valuation"
    ws["A15"].font, ws["A15"].fill, ws["B15"].fill = white, navy, navy
    for r, lab in zip(range(16, 22), VAL):
        ws[f"A{r}"] = lab
        ws[f"B{r}"].number_format = "#,##0.00" if r == 21 else "#,##0.0"
    for r in (19, 21):
        ws[f"A{r}"].font = ws[f"B{r}"].font = Font(bold=True, size=12)
    ws["B21"].fill = PatternFill("solid", fgColor="FFF2CC")
    ws["D16"] = "Value per share ($): WACC (across) x terminal growth (down)"
    ws["D16"].font = Font(bold=True)
    for j, w in enumerate(S_WACC):
        c = ws[f"{COLS[j]}17"]
        c.value, c.number_format, c.font, c.fill, c.alignment = w, "0.0%", white, navy, Alignment(horizontal="center")
    for i, g in enumerate(S_G):
        c = ws[f"E{18 + i}"]
        c.value, c.number_format, c.font = g, "0.0%", Font(bold=True)
        for col in COLS:
            ws[f"{col}{18 + i}"].number_format = "#,##0.00"
    for col, w in zip("ABCDEFGHIJK", (31, 9, 2, 23, 9, 9, 9, 9, 9, 9, 2)):
        ws.column_dimensions[col].width = w
    wb.save(path)


TASK = (
    "Build the DCF in the open Excel workbook DCF.xlsx (sheet DCF) as fast as you can: speed matters most, so do it in "
    "one step if you can. Script Excel itself with AppleScript (tell application \"Microsoft Excel\" ... set formula "
    "of range ...): one script can set every formula, the sensitivity block included. Never edit the .xlsx file with another program. Every output must "
    "be an Excel formula that refers to the sheet's inputs, never a typed number.\n"
    "Assumptions are in B4:B13. The projections are in D3:J14: columns F to J are the years 2026E to 2030E (row 4 holds "
    "the year number 1 to 5), column E is 2025A and E5 is 2025 revenue. The formulas for 2026E, column F (columns G to J "
    "are the same formulas filled right):\nF5 Revenue = E5*(1+$B$4)\nF6 EBITDA = F5*$B$5\nF7 D&A = F5*$B$6\n"
    "F8 EBIT = F6-F7\nF9 Taxes on EBIT = F8*$B$9\nF10 Capex = F5*$B$7\nF11 Increase in NWC = (F5-E5)*$B$8\n"
    "F12 Unlevered free cash flow = F8-F9+F7-F10-F11\nF13 Discount factor = 1/(1+$B$10)^F4\n"
    "F14 PV of free cash flow = F12*F13\n"
    "Valuation, B16:B21: B16 = SUM(F14:J14); B17 terminal value = J12*(1+B11)/(B10-B11); B18 = B17*J13; B19 enterprise "
    "value = B16+B18; B20 equity value = B19-B12; B21 value per share = B20/B13.\n"
    "Sensitivity, F18:J22: value per share at the WACC in row 17 (F17:J17) and the terminal growth in column E "
    "(E18:E22), one formula with $ anchors filled over the block: (the free cash flows F12:J12 each discounted at that "
    "WACC by its year in row 4, plus the terminal value J12*(1+g)/(WACC-g) discounted five years, minus net debt B12) "
    "divided by diluted shares B13.\n"
    "Leave the workbook open; do not save it.")


def check_live() -> dict:
    want = expected()
    script = ('tell application "Microsoft Excel"\nset ws to worksheet "DCF" of workbook "DCF.xlsx"\nset out to ""\n'
              + "".join(f'set out to out & "{r}" & "|" & (formula of range "{r}" of ws) & "|" & ((value of range "{r}" of ws) as text) & linefeed\n'
                        for r in want) + "return out\nend tell")
    res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=60).stdout
    got = {}
    for line in res.splitlines():
        p = line.split("|")
        if len(p) == 3:
            try:
                got[p[0]] = (p[1], float(p[2]))
            except ValueError:
                got[p[0]] = (p[1], None)
    bad = []
    for ref, w in want.items():
        f, x = got.get(ref, (None, None))
        if not (isinstance(f, str) and f.startswith("=") and isinstance(x, float) and abs(x - w) <= 0.005 * abs(w) + 1e-9):
            bad.append({"cell": ref, "want": round(w, 3), "value": x, "formula": f})
    charts = subprocess.run(["osascript", "-e", 'tell application "Microsoft Excel" to count of chart objects of worksheet "DCF" of workbook "DCF.xlsx"'],
                            capture_output=True, text=True, timeout=30).stdout.strip()
    return {"cells": len(want), "right": len(want) - len(bad), "charts": charts, "passed": not bad,
            "wrong": bad[:10], "value_per_share": round(want["B21"], 2)}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "task"
    if cmd == "make":
        p = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "DCF.xlsx"
        make(p)
        print(p)
    elif cmd == "live":
        print(json.dumps(check_live(), indent=1))
    else:
        print(TASK)

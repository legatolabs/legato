"""Read the text of the foreground window on Windows, without a screenshot: the window's UI Automation tree (what
screen readers use; Edge, Chrome and Firefox pages come through it too) as the outline `desktop_read` gives on a Mac.
It needs nothing installed: PowerShell 5, which ships with Windows, has UI Automation.

    from legato import winread
    from legato.tools import local_shell
    print(winread.read(local_shell))      # window "Untitled - Notepad" / - textbox "Text Editor": "hello" ...
"""
from __future__ import annotations

import json
import re
from itertools import chain, pairwise
from typing import Callable

# The walk, in PowerShell (written without double quotes: it travels as one command-line argument). It prints one
# line per element, `depth<TAB>type<TAB>flags<TAB>name<TAB>value`, after a first line naming the window or page, and
# a last `#stopped` line if it ran into a bound. Each element's properties arrive with its parent's children in one
# call (a CacheRequest), which is what keeps a page of a thousand elements to a second or two.
SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$A = [System.Windows.Automation.AutomationElement]
$S = [System.Windows.Automation.TreeScope]
$C = [System.Windows.Automation.ControlType]
$PC = [System.Windows.Automation.PropertyCondition]
$VP = [System.Windows.Automation.ValuePattern]::ValueProperty
$TG = [System.Windows.Automation.TogglePattern]::ToggleStateProperty
$SL = [System.Windows.Automation.SelectionItemPattern]::IsSelectedProperty
$maxNodes = __NODES__; $maxText = __TEXT__; $maxOut = __OUT__; $maxDepth = __DEPTH__; $maxMs = __MS__
function Esc([string]$s) {
  if ($s.Length -gt $maxText) { $s = $s.Substring(0, $maxText) }
  $s.Replace('\', '\\').Replace([string][char]9, '\t').Replace([string][char]13, '').Replace([string][char]10, '\n')
}
function Val($e, $p) { $v = $e.GetCachedPropertyValue($p, $true); if ($v -eq $A::NotSupported) { $null } else { $v } }
$cr = New-Object System.Windows.Automation.CacheRequest
foreach ($p in $A::NameProperty, $A::ControlTypeProperty, $A::IsEnabledProperty, $A::HasKeyboardFocusProperty,
               $A::IsPasswordProperty, $A::FrameworkIdProperty, $A::ClassNameProperty,
               $A::LocalizedControlTypeProperty, $VP, $TG, $SL) { $cr.Add($p) }
$cr.TreeScope = $S::Element -bor $S::Children
$cr.TreeFilter = [System.Windows.Automation.Automation]::ControlViewCondition
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$desk = $A::RootElement
$win = $A::FocusedElement
if ($win -eq $null) { $win = $desk }
while ($true) {
  $up = $walker.GetParent($win)
  if ($up -eq $null -or [System.Windows.Automation.Automation]::Compare($up, $desk)) { break }
  $win = $up
}
$web = New-Object System.Windows.Automation.AndCondition (New-Object $PC ($A::ControlTypeProperty, $C::Document)),
  (New-Object System.Windows.Automation.OrCondition (New-Object $PC ($A::FrameworkIdProperty, 'Chrome')),
    (New-Object $PC ($A::FrameworkIdProperty, 'Gecko')))
$page = $win.FindFirst($S::Descendants, $web)
$class = [string]$win.Current.ClassName
for ($i = 0; $page -eq $null -and $i -lt 4 -and ($class.StartsWith('Chrome_') -or $class.StartsWith('Mozilla')); $i++) {
  Start-Sleep -Milliseconds 400   # a browser builds its page's tree when first asked for it
  $page = $win.FindFirst($S::Descendants, $web)
}
$clock = [System.Diagnostics.Stopwatch]::StartNew()
if ($page -ne $null) {
  $top = $page.GetUpdatedCache($cr)
  [Console]::Out.WriteLine('#page' + [char]9 + (Esc $top.Cached.Name) + [char]9 + (Esc ([string](Val $top $VP))))
} else {
  $top = $win.GetUpdatedCache($cr)
  [Console]::Out.WriteLine('#window' + [char]9 + (Esc $top.Cached.Name))
}
$todo = New-Object System.Collections.Stack
$kids = $top.CachedChildren
for ($i = $kids.Count - 1; $i -ge 0; $i--) { $todo.Push(@($kids[$i], 0)) }
$seen = 0; $out = 0
while ($todo.Count -gt 0) {
  if ($seen -ge $maxNodes -or $out -ge $maxOut -or $clock.ElapsedMilliseconds -gt $maxMs) {
    [Console]::Out.WriteLine('#stopped' + [char]9 + $seen); break
  }
  $item = $todo.Pop(); $e = $item[0]; $depth = $item[1]; $seen++
  try {
    $type = $e.Cached.ControlType.ProgrammaticName.Replace('ControlType.', '')
    $name = [string]$e.Cached.Name
    if ($type -eq 'Pane' -and $e.Cached.FrameworkId -eq 'Win32') { $type = 'Pane/' + $e.Cached.ClassName }
    elseif ($e.Cached.LocalizedControlType -eq 'heading') { $type = 'Heading' }
    $value = Val $e $VP
    $flags = ''
    if (-not $e.Cached.IsEnabled) { $flags += 'd' }
    if ($e.Cached.HasKeyboardFocus) { $flags += 'f' }
    if ((Val $e $SL) -eq $true) { $flags += 's' }
    $on = Val $e $TG
    if ($on -ne $null) { if ([int]$on -eq 1) { $flags += 'c' } else { $flags += 'u' } }
    $more = $type -ne 'Text' -and $type -ne 'Heading'
    if ($e.Cached.IsPassword) { $value = $null; $flags += 'p' }
    elseif ($value -eq $null -and ($type -eq 'Document' -or $type -eq 'Edit') -and
            $e.Cached.FrameworkId -ne 'Chrome' -and $e.Cached.FrameworkId -ne 'Gecko') {
      try {   # a rich text box (WordPad, the new Notepad) has no value: its text is in the text pattern
        $value = $e.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText($maxText)
        $more = $false
      } catch {}
    }
    $line = [string]$depth + [char]9 + $type + [char]9 + $flags + [char]9 + (Esc $name) + [char]9 + (Esc ([string]$value))
    [Console]::Out.WriteLine($line); $out += $line.Length
    if ($more -and $depth -lt $maxDepth) {
      $kids = $e.GetUpdatedCache($cr).CachedChildren
      for ($i = $kids.Count - 1; $i -ge 0; $i--) { $todo.Push(@($kids[$i], ($depth + 1))) }
    }
  } catch {}   # an element that went away while it was read
}
"""

ROLE_NAMES = {"Text": "text", "Edit": "textbox", "Document": "textbox", "Hyperlink": "link", "CheckBox": "checkbox",
              "RadioButton": "radio", "ComboBox": "combobox", "MenuItem": "menuitem", "MenuBar": "menubar",
              "ListItem": "listitem", "TabItem": "tab", "TreeItem": "treeitem", "DataItem": "row",
              "HeaderItem": "columnheader", "SplitButton": "menubutton", "ToolBar": "toolbar",
              "StatusBar": "statusbar", "TitleBar": "titlebar", "ProgressBar": "progress", "ToolTip": "tooltip"}
TEXT_TYPES = {"Edit", "Document"}
# a classic control that came without its control type (a bare pane with its window class, its text as its name)
CLASSES = {"edit": "Edit", "static": "Text", "button": "Button", "combobox": "ComboBox", "listbox": "List",
           "msctls_statusbar32": "StatusBar", "systreeview32": "Tree", "syslistview32": "List"}
# containers that get no line of their own unless they are named, and parts that are never worth a line
QUIET_TYPES = {"Pane", "Group", "Custom", "ScrollBar", "Thumb", "Separator", "Image", "TitleBar", "Window"}
SKIP_TYPES = {"ScrollBar", "Thumb", "TitleBar"}                # left out with all they hold


def script(max_chars: int = 8000, max_nodes: int = 4000, max_depth: int = 60, max_s: float = 8.0) -> str:
    """The PowerShell that prints the foreground window's elements; `outline` turns its output into the text."""
    text = min(max(int(max_chars), 1), 200_000)
    out = SCRIPT
    for k, v in (("__NODES__", max_nodes), ("__TEXT__", text), ("__OUT__", 4 * text + 4000), ("__DEPTH__", max_depth),
                 ("__MS__", int(max_s * 1000))):
        out = out.replace(k, str(int(v)))
    return out.strip()


_ESCAPE = re.compile(r"\\(.)", re.DOTALL)
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}


def _unesc(s: str) -> str:
    if "\\" not in s:
        return s
    return _ESCAPE.sub(lambda m: _ESCAPES.get(m[1], m[1]), s)


def _quote(s: str, cap: int = 2000) -> str:
    return json.dumps(s if len(s) <= cap else s[:cap] + "…", ensure_ascii=False)


def _line(kind: str, name: str, value: str, flags: str, kids: bool) -> str | None:
    """One element as `- role "name": value [states]`, or None when it says nothing (the shape of the Mac outline)."""
    if kind in QUIET_TYPES and not name:
        return None
    role = "password" if "p" in flags else ROLE_NAMES.get(kind, kind.lower() or "element")
    out = f"- {role}" + (f" {_quote(name)}" if name else "")
    states = []
    if kind in TEXT_TYPES and "p" not in flags:
        out += f": {_quote(value)}"
    elif value and value != name and kind != "Hyperlink":     # a link's value is its address: too long to list
        out += f": {_quote(value)}"
    if "c" in flags or "u" in flags:
        states.append("checked" if "c" in flags else "not checked")
    if "f" in flags:
        states.append("focused")
    if "d" in flags:
        states.append("disabled")
    if "s" in flags:
        states.append("selected")
    if not kids and not states and out == f"- {role}":
        return None
    return out + (f" [{', '.join(states)}]" if states else "")


def outline(raw: str, max_chars: int = 8000) -> str:
    """The script's output as the outline: a first line `window "Title"` or `page "Title" https://...`, then one
    line per element that says something, indented under what holds it. At most `max_chars` characters, the note
    saying it was cut included. Raises RuntimeError when `raw` is not the script's output (PowerShell failed)."""
    max_chars = max(1, int(max_chars))
    rows = iter(raw.replace("\r\n", "\n").split("\n"))
    header = next((row for row in rows if row.startswith(("#page\t", "#window\t"))), None)
    if header is None:
        raise RuntimeError("could not read the foreground window through UI Automation: "
                           + (raw.strip()[-300:] or "PowerShell printed nothing"))
    first = header.split("\t")
    title = _unesc(first[1]).strip()
    head = f"{first[0][1:]} {_quote(title or 'untitled')}"
    if first[0] == "#page" and len(first) > 2 and first[2].strip():
        head += f" {_unesc(first[2]).strip()}"
    stopped = ""

    def elements():
        nonlocal stopped
        for row in rows:
            if row.startswith("#stopped"):
                stopped = f"[stopped after {row.split(chr(9))[-1]} elements]"
                break
            fields = row.split("\t")
            if len(fields) >= 5 and fields[0].isdigit():
                yield int(fields[0]), fields[1], fields[2], fields[3], fields[4]

    note = f"[cut at {max_chars} characters: ask for more with max_chars]"
    if len(note) * 3 > max_chars:
        note = "[cut]"
    lines, size, cut, last = [head], len(head), "", ""
    shown: list[tuple[int, int, str]] = []        # the lines above this element: (depth in the tree, indent, name)
    skip_below: int | None = None
    # One row of lookahead tells us whether an unnamed control has children.
    for (depth, kind, flags, name, value), following in pairwise(chain(elements(), (None,))):
        if skip_below is not None and depth > skip_below:
            continue
        skip_below = None
        while shown and shown[-1][0] >= depth:
            shown.pop()
        if kind in SKIP_TYPES:
            skip_below = depth
            continue
        name, value = _unesc(name).strip(), _unesc(value).strip()
        if kind.startswith("Pane/"):
            kind = "Edit" if "richedit" in kind.lower() else CLASSES.get(kind[5:].lower(), "Pane")
            if kind == "Edit":
                name, value = "", name
        above = shown[-1][2] if shown else ""
        kids = following is not None and following[0] > depth
        if kind == "Text":
            if not name or name in (above, last):
                continue                          # empty, or the text of what holds it or of the line before
            line = f"- text {_quote(name)}"
        else:
            if kind in QUIET_TYPES and name in (title, above):
                name = ""                         # a pane named as the window or as what holds it
            line = _line(kind, name, value, flags, kids)
            if line is None:                      # says nothing: what it holds takes its place
                continue
        indent = shown[-1][1] + 1 if shown else 0
        line = "  " * indent + line
        if size + len(line) + 1 + len(note) + 1 > max_chars:
            cut = note
            break
        lines.append(line)
        size += len(line) + 1
        last = name
        shown.append((depth, indent, name))
    if len(lines) == 1 and not cut:
        lines.append("(no text: the window's UI Automation tree is empty; use a screenshot)")
    text = "\n".join(lines + ([cut or stopped] if cut or stopped else []))
    if len(text) > max_chars:                     # the first line alone is longer (a long address)
        text = text[:max_chars - 6] + "\n[cut]" if max_chars > 12 else text[:max_chars]
    return text


def read(shell: Callable[[str, float], str], max_chars: int = 8000, timeout_s: float = 20.0) -> str:
    """The foreground window's text as an outline, read through `shell(command, timeout_s) -> output`, a PowerShell
    on the Windows machine (`legato.tools.local_shell` on this one). In a browser it is the page only."""
    return outline(shell(script(max_chars), timeout_s), max_chars)

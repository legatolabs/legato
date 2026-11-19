"""An alias to /Applications for the .dmg window, carrying the Applications folder's icon: on macOS 26 Finder draws a
link or alias to /Applications inside a disk image as an empty dashed square unless the item has its own icon.
Made with Foundation and AppKit (no Finder), so it works on a build machine.

    python3 app/macos/dmg/make_alias.py <dir>      # writes <dir>/Applications
"""
import sys
from pathlib import Path

from AppKit import NSWorkspace
from Foundation import NSURL, NSURLBookmarkCreationSuitableForBookmarkFile

out = Path(sys.argv[1]) / "Applications"
target = NSURL.fileURLWithPath_("/Applications")
data, err = target.bookmarkDataWithOptions_includingResourceValuesForKeys_relativeToURL_error_(
    NSURLBookmarkCreationSuitableForBookmarkFile, None, None, None)
if data is None:
    sys.exit(f"bookmark for /Applications: {err}")
ok, err = NSURL.writeBookmarkData_toURL_options_error_(data, NSURL.fileURLWithPath_(str(out)), 0, None)
if not ok:
    sys.exit(f"writing the alias: {err}")
ws = NSWorkspace.sharedWorkspace()
if not ws.setIcon_forFile_options_(ws.iconForFile_("/Applications"), str(out), 0):
    sys.exit("setting the alias's icon failed")
print(out)

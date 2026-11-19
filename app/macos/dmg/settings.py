# dmgbuild settings for Legato's .dmg: the app and an alias to Applications (make_alias.py) on the background
# (background.py), as release.sh calls it:
#   dmgbuild -s settings.py -D app=<Legato.app> -D alias=<dir>/Applications -D background=<background.tiff> Legato <out.dmg>
import os.path

app = defines["app"]                                    # noqa: F821 (dmgbuild provides `defines`)
files = [app, defines["alias"]]                         # noqa: F821
background = defines["background"]                      # noqa: F821
format = "UDZO"
window_rect = ((200, 160), (660, 420))
icon_size = 112
text_size = 13
show_status_bar = show_tab_view = show_toolbar = show_pathbar = show_sidebar = False
default_view = "icon-view"
icon_locations = {os.path.basename(app): (170, 196), "Applications": (490, 196)}

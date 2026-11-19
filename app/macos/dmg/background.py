"""The .dmg window's background: light, so Finder's black icon labels read in light and dark mode; ink type, a headline, an arrow from the app to Applications, faint waves (the site's).
Writes background.png (660x420) and background@2x.png; release.sh joins them into one Retina TIFF.

    python3 app/macos/dmg/background.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 660, 420
APP, APPS = (170, 196), (490, 196)          # icon centres; settings.py puts the icons here
HERE = Path(__file__).parent
SERIF, MONO = "/System/Library/Fonts/NewYork.ttf", "/System/Library/Fonts/SFNSMono.ttf"


def draw(s: int) -> Image.Image:
    im = Image.new("RGB", (W * s, H * s), (247, 247, 245))
    glow = Image.new("L", im.size, 0)       # a soft light behind the two icons
    ImageDraw.Draw(glow).ellipse((80 * s, 60 * s, 580 * s, 340 * s), fill=60)
    im.paste((255, 255, 255), mask=glow.filter(ImageFilter.GaussianBlur(70 * s)))
    d = ImageDraw.Draw(im, "RGBA")
    for i in range(16):                      # the waves, low on the window
        f = i / 15
        a = int(18 + 34 * math.sin(math.pi * f))
        pts = []
        for x in range(-10, W + 11, 4):
            u = x / W
            y = 352 + 16 * math.sin(u * 3.1 + f * 1.4) + 7 * math.sin(u * 7.3 + f * 2.6) + (f - .5) * 18 * math.cos(u * 2.2)
            pts.append((x * s, y * s))
        d.line(pts, fill=(10, 10, 10, int(a * 0.55)), width=max(1, s // 2))
    head = ImageFont.truetype(SERIF, 28 * s)
    t = "Drag Legato into Applications"
    w = d.textlength(t, font=head)
    d.text(((W * s - w) / 2, 46 * s), t, font=head, fill=(10, 10, 10))
    x0, x1, y = (APP[0] + 78) * s, (APPS[0] - 78) * s, APP[1] * s   # the arrow, between the icons
    d.line((x0, y, x1, y), fill=(10, 10, 10, 170), width=max(2, int(1.5 * s)))
    d.line((x1 - 9 * s, y - 7 * s, x1, y), fill=(10, 10, 10, 170), width=max(2, int(1.5 * s)))
    d.line((x1 - 9 * s, y + 7 * s, x1, y), fill=(10, 10, 10, 170), width=max(2, int(1.5 * s)))
    mono = ImageFont.truetype(MONO, 11 * s)        # under the headline: Finder's path and status bars can cover the bottom
    c = "then open it from Applications and tap fn"
    w = d.textlength(c, font=mono)
    d.text(((W * s - w) / 2, 94 * s), c, font=mono, fill=(130, 130, 128))
    return im


if __name__ == "__main__":
    draw(1).save(HERE / "background.png")
    draw(2).save(HERE / "background@2x.png")
    print("wrote", HERE / "background.png", HERE / "background@2x.png")

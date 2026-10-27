"""Adopt 'SMI Masala logo.png' (the black-and-gold badge) as the app logo.

Three things need solving, not just a copy
------------------------------------------
1. The file has a WHITE BACKGROUND baked into the pixels (alpha 255 everywhere).
   Removing it by deleting "white pixels" would eat the cream-coloured
   "Shuddhata Ki Pehchaan" text inside the badge, so this floods inwards from
   the border instead. Text enclosed by the dark badge is never reached.
2. It is 1200x1000 - landscape. The UI styles .brand-logo with
   border-radius:50% (a circular crop), which would slice off the left and
   right gold edges, exactly the fault the previous artwork had. The CSS is
   changed to a rounded rectangle with object-fit:contain so a landscape badge
   shows whole.
3. favicon and .ico must be rebuilt from this same artwork or the tab and the
   .exe would keep showing the old red/green badge.

The user's original file is copied to a stable name, never modified.
"""

import os
import shutil
import sys
from collections import deque
from datetime import datetime

ROOT = r"C:\Shresth_Masala_Application"
sys.path.insert(0, ROOT)
from PIL import Image  # noqa: E402

ASSETS = os.path.join(ROOT, "frontend", "assets")
SRC_NAME = "SMI Masala logo.png"
SRC = os.path.join(ASSETS, SRC_NAME)
KEEP = os.path.join(ASSETS, "logo-black-gold-source.png")
DST = os.path.join(ASSETS, "logo.png")
FAV = os.path.join(ASSETS, "favicon.png")
ICO = os.path.join(ASSETS, "icon.ico")

# The background is a warm off-white, not pure white - (247, 244, 235) in the
# supplied file. Rather than guess a white threshold, the corner pixel is
# sampled and used as the key, so this works for any flat backdrop colour.
BG_TOL = 34  # max per-channel distance from the sampled key
WEB_HEIGHT = 128  # landscape badge: size by height, width follows
FAVICON_SIZE = 64
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _is_background(r, g, b, key):
    return abs(r - key[0]) <= BG_TOL and abs(g - key[1]) <= BG_TOL and abs(b - key[2]) <= BG_TOL


def strip_white_border(im):
    """Flood inwards from the edges removing background-coloured pixels only.

    A border flood fill (rather than a global colour key) is what keeps
    enclosed light detail safe: the cream "Shuddhata Ki Pehchaan" tagline sits
    inside a dark badge and is a similar colour to the backdrop, so a global key
    would punch holes through the text. Flooding inwards never reaches it.
    """
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()

    # Sample the four corners and take the most common as the background key.
    samples = [px[x, y][:3] for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
    key = max(set(samples), key=samples.count)
    print(f"  background key    : {key}")

    seen = bytearray(w * h)
    q = deque()

    def push(x, y):
        if not seen[y * w + x] and _is_background(*px[x, y][:3], key):
            seen[y * w + x] = 1
            q.append((x, y))

    for x in range(w):  # top and bottom edges
        push(x, 0)
        push(x, h - 1)
    for y in range(h):  # left and right edges
        push(0, y)
        push(w - 1, y)

    removed = 0
    while q:
        x, y = q.popleft()
        r, g, b, _ = px[x, y]
        if not _is_background(r, g, b, key):
            continue
        px[x, y] = (r, g, b, 0)
        removed += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                seen[ny * w + nx] = 1
                q.append((nx, ny))
    return im, removed


def main():
    if not os.path.exists(SRC):
        print(f"missing source: {SRC}")
        return 1

    # Preserve the user's file under a stable name, and keep a backup of the
    # artwork we are replacing.
    if not os.path.exists(KEEP):
        shutil.copy2(SRC, KEEP)
        print("  preserved original as:", os.path.basename(KEEP))
    bakdir = os.path.join(ROOT, "data", "backups")
    os.makedirs(bakdir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(DST):
        shutil.copy2(DST, os.path.join(bakdir, f"logo_old_{stamp}.png"))
    shutil.copy2(SRC, os.path.join(bakdir, f"logo_source_{stamp}.png"))

    im = Image.open(KEEP)
    print("  source            :", im.size, im.mode)
    im, removed = strip_white_border(im)
    bbox = im.getchannel("A").getbbox()
    im = im.crop(bbox)
    print(f"  white background  : {removed:,} px made transparent")
    print("  trimmed to        :", im.size)

    # Size by height so a landscape badge keeps its proportions.
    w, h = im.size
    nw = max(1, round(w * WEB_HEIGHT / h))
    web = im.resize((nw, WEB_HEIGHT), Image.LANCZOS)
    web.save(DST, "PNG", optimize=True)
    print(f"  logo.png          : {web.size} {os.path.getsize(DST):,} bytes")

    # Favicon: pad the landscape badge into a square so it is not squashed and
    # the whole mark survives at 16px.
    def square(src_im, size):
        side = max(src_im.size)
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
        canvas.paste(src_im, ((side - src_im.size[0]) // 2, (side - src_im.size[1]) // 2), src_im)
        return canvas.resize((size, size), Image.LANCZOS)

    square(im, FAVICON_SIZE).save(FAV, "PNG", optimize=True)
    print(f"  favicon.png       : {FAVICON_SIZE}x{FAVICON_SIZE} {os.path.getsize(FAV):,} bytes")

    sq = square(im, 256)
    sq.save(ICO, format="ICO", sizes=ICO_SIZES)
    print(f"  icon.ico          : multi-size {os.path.getsize(ICO):,} bytes")

    lo, hi = im.getchannel("A").getextrema()
    print(f"  alpha range       : {lo}-{hi} ({'transparent' if lo == 0 else 'OPAQUE'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

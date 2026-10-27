"""Swap in 'SMI LOGO.jpg' as the company logo and retire the old artwork.

Tricker than the previous swap
------------------------------
1. It is a JPEG, so there is no alpha channel at all - the white background is
   baked in and must be removed.
2. The artwork is MOSTLY white/cream internally (the filigree is cream on a
   white ground, and SMI sits on white). A global colour key would delete the
   interior and leave a shredded badge. A flood fill inward from the border is
   the only safe method: it strips the exterior and stops dead at the gold rim,
   leaving every enclosed white untouched. The top edge is fully open, so the
   fill gets in.
3. It is 1175x912 - landscape - so it is sized by height, width following.
   The CSS is already aspect-agnostic (object-fit: contain, 4px radius), so no
   styling change is needed.

KNOWN DEFECT, NOT FIXABLE HERE
-----------------------------
The supplied file is cropped at the bottom: the badge's lower gold rim and the
base of the "EST. 2020" plaque run off the image edge. Cropped pixels cannot be
recovered, so the result has a flat cut along the bottom. The source needs
replacing to fix it; the script reports it rather than hiding it.
"""

import os
import shutil
import sys
from collections import deque
from datetime import datetime

ROOT = r"C:\Shresth_Masala_Application"
sys.path.insert(0, ROOT)
from PIL import Image  # noqa: E402

BRANDING = os.path.join(ROOT, "branding")
ASSETS = os.path.join(ROOT, "frontend", "assets")
BACKUPS = os.path.join(ROOT, "data", "backups")
SRC = os.path.join(BRANDING, "SMI LOGO.jpg")
DST = os.path.join(ASSETS, "logo.png")
FAV = os.path.join(ASSETS, "favicon.png")
ICO = os.path.join(ASSETS, "icon.ico")

BG_TOL = 34  # per-channel distance from the sampled key
MIN_BG = 225  # a pixel counts as background if all channels are >= this
WEB_HEIGHT = 132  # landscape badge: size by height
FAVICON_SIZE = 64
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _is_bg(r, g, b, key):
    return (
        min(r, g, b) >= MIN_BG
        and abs(r - key[0]) <= BG_TOL
        and abs(g - key[1]) <= BG_TOL
        and abs(b - key[2]) <= BG_TOL
    )


def strip_border(im):
    """Flood inward from the edges, removing only background-coloured pixels."""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    samples = [px[x, y][:3] for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2))]
    key = max(set(samples), key=samples.count)
    print(f"  background key    : {key}")

    seen = bytearray(w * h)
    q = deque()

    def push(x, y):
        if not seen[y * w + x] and _is_bg(*px[x, y][:3], key):
            seen[y * w + x] = 1
            q.append((x, y))

    for x in range(w):
        push(x, 0)
        push(x, h - 1)
    for y in range(h):
        push(0, y)
        push(w - 1, y)

    removed = 0
    while q:
        x, y = q.popleft()
        r, g, b, _ = px[x, y]
        if not _is_bg(r, g, b, key):
            continue
        px[x, y] = (r, g, b, 0)
        removed += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx]:
                seen[ny * w + nx] = 1
                q.append((nx, ny))
    return im, removed


def square(im, size):
    side = max(im.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2), im)
    return canvas.resize((size, size), Image.LANCZOS)


def bottom_is_cut(im, key):
    """True if artwork runs into the bottom edge, i.e. the source is cropped."""
    w, h = im.size
    hit = sum(1 for x in range(0, w, 3) if not _is_bg(*im.load()[x, h - 1][:3], key))
    return hit > 0, hit


def main():
    if not os.path.exists(SRC):
        print(f"missing source: {SRC}")
        return 1
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for p, tag in ((DST, "logo_old"), (FAV, "favicon_old"), (ICO, "icon_old")):
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(BACKUPS, f"{tag}_{stamp}{os.path.splitext(p)[1]}"))
    print("  previous logo, favicon and .ico backed up")

    src = Image.open(SRC)
    print("  source            :", src.size, src.mode, src.format)

    im = Image.open(SRC)
    samples = [
        im.convert("RGB").getpixel(p)
        for p in (
            (1, 1),
            (im.size[0] - 2, 1),
            (1, im.size[1] - 2),
            (im.size[0] - 2, im.size[1] - 2),
        )
    ]
    key = max(set(samples), key=samples.count)
    cut, hits = bottom_is_cut(im.convert("RGBA"), key)
    if cut:
        print(
            f"  WARNING source cropped: artwork runs into the bottom edge "
            f"({hits} sampled px). Cannot be recovered - see the file."
        )

    stripped, removed = strip_border(im)
    box = stripped.getchannel("A").getbbox()
    im = stripped.crop(box)
    print(f"  background removed : {removed:,} px")
    print("  trimmed to        :", im.size)

    w, h = im.size
    nw = max(1, round(w * WEB_HEIGHT / h))
    web = im.resize((nw, WEB_HEIGHT), Image.LANCZOS)
    web.save(DST, "PNG", optimize=True)
    print(f"  logo.png          : {web.size} {os.path.getsize(DST):,} bytes")

    square(im, FAVICON_SIZE).save(FAV, "PNG", optimize=True)
    print(f"  favicon.png       : {FAVICON_SIZE}x{FAVICON_SIZE} {os.path.getsize(FAV):,} bytes")
    square(im, 256).save(ICO, format="ICO", sizes=ICO_SIZES)
    print(f"  icon.ico          : {os.path.getsize(ICO):,} bytes")

    lo, hi = im.getchannel("A").getextrema()
    print(f"  alpha range       : {lo}-{hi} ({'transparent' if lo == 0 else 'OPAQUE'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

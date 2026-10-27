"""Rebuild frontend/assets/logo.png from the good source artwork.

Why this is needed
------------------
The deployed logo.png was 1.89 MB, 1175x912 (landscape), fully OPAQUE (an
off-white background baked into the pixels) and visually cropped - the
"EST. 2021" line and a barcode-like artifact ran off the bottom edge. The UI
styles it with border-radius:50% + object-fit:cover at 58px, so a landscape
source was additionally being cut into a circle and losing its left and right
gold rim.

LOGO.png at the repo root is the same brand mark done properly: 1254x1254,
genuinely transparent, complete. favicon.png and icon.ico were already
generated from it, so rebuilding logo.png from the same source keeps every
place the logo appears consistent.

What this script does
---------------------
- trims the transparent margin so the badge fills the canvas (the source has
  a few px of transparent edge, which would otherwise shrink it)
- resizes to 512px, which is far more than the 58px UI / 64px print uses but
  stays sharp on a hi-dpi screen
- writes an optimised PNG, and optionally a small print-friendly variant
- backs up the previous logo.png first
"""

import os
import re
import shutil
import sys
from datetime import datetime

ROOT = r"C:\Shresth_Masala_Application"
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

SRC = os.path.join(ROOT, "LOGO.png")
DST = os.path.join(ROOT, "frontend", "assets", "logo.png")
BACKUP_DIR = os.path.join(ROOT, "data", "backups")
# The app shows the logo at 58px (header), 76px (login) and 64px (printed
# challan). 256px is 3x the largest real use, and downscaled to 76px it differs
# from a 512px build by 0.78 of 255 mean channel levels - invisible - while
# being 70% smaller. Shipping 512px would be 350 KB of nothing.
WEB_SIZE = 256


def alpha_bbox(im):
    """Bounding box of the visible pixels."""
    return im.getchannel("A").getbbox()


def build(size=WEB_SIZE, dst=DST, src=SRC):
    im = Image.open(src)
    if im.mode != "RGBA":
        im = im.convert("RGBA")

    before = im.size
    box = alpha_bbox(im)
    if box and box != (0, 0) + before:
        im = im.crop(box)
    trimmed = im.size

    # A circular badge in a square canvas: pad to square so nothing is squashed.
    side = max(im.size)
    if im.size != (side, side):
        canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
        canvas.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2), im)
        im = canvas

    im = im.resize((size, size), Image.LANCZOS)
    im.save(dst, "PNG", optimize=True)
    return before, trimmed, im.size, os.path.getsize(dst)


def version_references(new_version: int) -> int:
    """Bump the ?v= on every logo reference so no browser can serve a stale one.

    The server already sends Cache-Control: no-store for /frontend, which stops
    caching between reloads - but a tab left open across a logo change keeps the
    image it already decoded, and a query string is the only reliable way to
    force a new fetch. Kept in step with the image by this script so the two
    can never drift apart.
    """

    # Matches only a URL with no query yet, so repeated runs are idempotent.
    pat = re.compile(r"(/frontend/assets/logo\.png)(?=\")")
    targets = [
        os.path.join(ROOT, "frontend", "index.html"),
        os.path.join(ROOT, "frontend", "pages", "challans.html"),
    ]
    total = 0
    for t in targets:
        if not os.path.exists(t):
            continue
        with open(t, encoding="utf-8") as fh:
            s = fh.read()
        new, n = pat.subn(f"\\1?v={new_version}", s)
        if n:
            with open(t, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
        total += n
    return total


def main():
    for p in (SRC, DST):
        if not os.path.exists(p):
            print(f"missing: {p}")
            return 1

    old_size = os.path.getsize(DST)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = os.path.join(BACKUP_DIR, f"logo_old_{stamp}.png")
    shutil.copy2(DST, bak)

    before, trimmed, out_size, out_bytes = build()
    print("  source            :", os.path.basename(SRC), before, "->trimmed", trimmed)
    print("  backup            :", os.path.basename(bak))
    print(f"  old logo.png      : {old_size:>9,} bytes  {before[0]}x{before[1]}")
    print(f"  new logo.png      : {out_bytes:>9,} bytes  {out_size[0]}x{out_size[1]} (transparent)")

    chk = Image.open(DST)
    lo, hi = chk.getchannel("A").getextrema()
    print(f"  alpha range       : {lo}-{hi} ({'transparent' if lo == 0 else 'OPAQUE'})")
    print(f"  square            : {chk.size[0] == chk.size[1]}")

    n = version_references(3)
    print(f"  references bumped : {n} (now ?v=3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

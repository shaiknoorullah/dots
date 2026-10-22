"""Build dots-icons.ttf: Phosphor SVGs as a monospace fallback font.

Run under fontforge's python:  fontforge -lang=py -script build.py <src> <map> <out.ttf>

Metrics are copied from JetBrainsMono Nerd Font (em 1000, ascent 800, descent 200,
capHeight 730, advance 600) so a glyph served by fontconfig fallback sits on the same
baseline and occupies exactly one cell.

Scaling is driven by each glyph's measured bounding box AFTER import, never by assuming how
fontforge interpreted the SVG's viewBox -- Phosphor ships viewBox="0 0 256 256" with no
width/height, and the import scale for that is not something to bet a build on.
"""
import os
import re
import sys
import tempfile

import fontforge
import psMat

SRC, MAPPING, OUT = sys.argv[1], sys.argv[2], sys.argv[3]

EM, ASCENT, DESCENT = 1000, 800, 200
CAP = 730          # JetBrainsMono capHeight: the band an icon should visually occupy
ADVANCE = 600      # one monospace cell
BOX = 600          # icon side == one full cell. 540 left the glyphs visibly smaller than
                   # the capitals next to them (540 vs capHeight 730). A single monospace
                   # cell is 600 units, so this is the largest an icon can be without
                   # spilling into the neighbouring cell in a terminal.

f = fontforge.font()
f.fontname, f.familyname = "dotsIcons", "dots Icons"
f.fullname = "dots Icons"
f.em, f.ascent, f.descent = EM, ASCENT, DESCENT
f.encoding = "UnicodeFull"
f.copyright = "Glyph outlines from Phosphor Icons (MIT), github.com/phosphor-icons/core"

TMP = tempfile.mkdtemp()
rows = []
for line in open(MAPPING, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.startswith("#"):
        continue
    name, ph, weight, cp = line.split("\t")
    rows.append((name, ph, weight, int(cp, 16)))

n = 0
for name, ph, weight, cp in rows:
    rel = f"assets/regular/{ph}.svg" if weight == "regular" else f"assets/{weight}/{ph}-{weight}.svg"
    path = f"{SRC}/{rel}"

    if weight == "duotone":
        # Duotone draws a background shape at opacity="0.2". A font glyph has no opacity,
        # so that path would import as a second SOLID shape and bury the icon. Keep the
        # foreground only -- the result is the regular outline, which is the readable half.
        svg = open(path, encoding="utf-8").read()
        svg = re.sub(r'<path[^>]*opacity="0?\.\d+"[^>]*/>', "", svg)
        tmp = os.path.join(TMP, f"{name}.svg")
        open(tmp, "w", encoding="utf-8").write(svg)
        path = tmp

    g = f.createChar(cp, name)
    g.importOutlines(path, scale=False, correctdir=True)

    xmin, ymin, xmax, ymax = g.boundingBox()
    w, h = xmax - xmin, ymax - ymin
    if w <= 0 or h <= 0:
        raise SystemExit(f"{name}: imported {rel} with an empty bounding box")

    s = BOX / max(w, h)
    g.transform(psMat.scale(s))
    xmin, ymin, xmax, ymax = g.boundingBox()
    # centre horizontally in the cell, and vertically on the cap band (baseline..CAP)
    g.transform(psMat.translate(
        (ADVANCE - (xmax - xmin)) / 2.0 - xmin,
        (CAP - (ymax - ymin)) / 2.0 - ymin,
    ))
    g.width = ADVANCE
    g.round()
    n += 1

if n != len(rows):
    raise SystemExit(f"built {n} of {len(rows)} glyphs")
f.generate(OUT)

# manifest: the only way to find a glyph by name later without re-deriving the registry
with open(OUT + ".manifest.tsv", "w", encoding="utf-8") as m:
    for name, ph, weight, cp in rows:
        m.write(f"{name}\t{ph}\t{weight}\tU+{cp:X}\t{chr(cp)}\n")

print(f"dots-icons: {n} glyphs, em {EM}, advance {ADVANCE}")

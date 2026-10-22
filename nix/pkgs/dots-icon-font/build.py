"""Build dots-icons.ttf: four monochrome icon sets as one monospace fallback font.

    fontforge -lang=py -script build.py <out.ttf> <registry.tsv> <phosphor-src> <icons.json>...

Metrics copy JetBrainsMono Nerd Font (em 1000, ascent 800, descent 200, capHeight 730,
advance 600) so a glyph served by fontconfig fallback sits on the same baseline and fills
exactly one cell.

Scaling is driven by each glyph's measured bounding box AFTER import, never by assuming how
fontforge read the source viewBox -- the four sets use 256, 24, 128 and 16 unit grids, and
betting a build on import heuristics across all of them is how you get silent garbage.
"""
import json, os, re, sys, tempfile

import fontforge
import psMat

OUT, REG, PH_SRC = sys.argv[1], sys.argv[2], sys.argv[3]
BUNDLES = sys.argv[4:]

EM, ASCENT, DESCENT = 1000, 800, 200
CAP = 730          # JetBrainsMono capHeight: the band an icon should visually occupy
ADVANCE = 600      # one monospace cell
BOX = 600          # icon side == the full cell; smaller reads undersized beside capitals

ICONIFY = {}
for b in BUNDLES:
    d = json.load(open(b, encoding="utf-8"))
    ICONIFY[d["prefix"]] = d

f = fontforge.font()
f.fontname, f.familyname, f.fullname = "dotsIcons", "dots Icons", "dots Icons"
f.em, f.ascent, f.descent = EM, ASCENT, DESCENT
f.encoding = "UnicodeFull"
f.copyright = ("Phosphor Icons (MIT); Simple Icons (CC0); Devicon (MIT); "
               "Codicons (CC BY 4.0)")

rows = []
for line in open(REG, encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.startswith("#"):
        continue
    name, src, icon, weight, cp = line.split("\t")
    rows.append((name, src, icon, weight, int(cp, 16)))

TMP = tempfile.mkdtemp()

def svg_path(name, src, icon, weight):
    """Return a path to an SVG on disk for this row, materialising one if needed."""
    if src == "ph":
        rel = f"assets/regular/{icon}.svg" if weight == "regular" \
              else f"assets/{weight}/{icon}-{weight}.svg"
        p = f"{PH_SRC}/{rel}"
        if weight != "duotone":
            return p
        # Duotone draws a background shape at opacity="0.2". A font glyph has no opacity,
        # so that path imports as a second SOLID shape and buries the icon. Keep the
        # foreground only, which is the readable half.
        body = re.sub(r'<path[^>]*opacity="0?\.\d+"[^>]*/>', "", open(p, encoding="utf-8").read())
    else:
        d = ICONIFY[src]
        ic = d["icons"][icon]
        w = ic.get("width", d.get("width", 16))
        h = ic.get("height", d.get("height", 16))
        body = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">'
                f'{ic["body"]}</svg>')
    out = os.path.join(TMP, f"{name}.svg")
    open(out, "w", encoding="utf-8").write(body)
    return out

n = 0
for name, src, icon, weight, cp in rows:
    g = f.createChar(cp, name)
    g.importOutlines(svg_path(name, src, icon, weight), scale=False, correctdir=True)

    bb = g.boundingBox()
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        # a handful of upstream icons are genuinely empty; a zero-width glyph in a
        # monospace font would collapse the cell, so give it the advance and move on
        g.width = ADVANCE
        n += 1
        continue

    g.transform(psMat.scale(BOX / max(w, h)))
    bb = g.boundingBox()
    g.transform(psMat.translate((ADVANCE - (bb[2] - bb[0])) / 2.0 - bb[0],
                                (CAP - (bb[3] - bb[1])) / 2.0 - bb[1]))
    g.width = ADVANCE
    g.round()
    n += 1

if n != len(rows):
    raise SystemExit(f"built {n} of {len(rows)} glyphs")
f.generate(OUT)

with open(OUT + ".manifest.tsv", "w", encoding="utf-8") as m:
    for name, src, icon, weight, cp in rows:
        m.write(f"{name}\t{src}\t{icon}\t{weight}\tU+{cp:X}\t{chr(cp)}\n")

print(f"dots-icons: {n} glyphs from {1 + len(ICONIFY)} sets, em {EM}, advance {ADVANCE}")

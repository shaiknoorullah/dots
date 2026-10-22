"""Build dots-icons.ttf: Phosphor plus Simple Icons as one monospace fallback font.

    fontforge -lang=py -script build.py <out.ttf> <registry.tsv> <phosphor-src> <icons.json>...

Metrics copy JetBrainsMono Nerd Font (em 1000, ascent 800, descent 200, capHeight 730,
advance 600) so a glyph served by fontconfig fallback shares the baseline and fills one cell.

OPTICAL CONSISTENCY is the whole point of how this scales. The obvious approach -- scale
each glyph by its own bounding box until it fills the cell -- is wrong: a dot and a folder
are drawn at deliberately different sizes inside the same canvas, and bbox scaling erases
that, rendering every icon at identical visual weight. So:

  1. every source is rewritten onto ONE shared 1000x1000 canvas, with the set's own grid
     (Phosphor 256, Simple Icons 24) mapped onto it and centred;
  2. a probe glyph -- a full-canvas square -- is imported once to measure exactly how
     fontforge scales that canvas, instead of guessing at its import heuristics;
  3. every glyph then gets the SAME transform derived from the probe.

An icon keeps the proportion its designer gave it, and two icons from different sets stay
comparable because both were fitted to the same canvas first.
"""
import json, os, re, sys, tempfile

import fontforge
import psMat

OUT, REG, PH_SRC = sys.argv[1], sys.argv[2], sys.argv[3]
BUNDLES = sys.argv[4:]

EM, ASCENT, DESCENT = 1000, 800, 200
CAP = 730          # JetBrainsMono capHeight: the band an icon should occupy
ADVANCE = 600      # one monospace cell
BOX = 600          # the shared canvas maps to this; icons sit inside it as designed
CANVAS = 1000.0    # the shared intermediate canvas

ICONIFY = {json.load(open(b, encoding="utf-8"))["prefix"]: json.load(open(b, encoding="utf-8"))
           for b in BUNDLES}

f = fontforge.font()
f.fontname, f.familyname, f.fullname = "dotsIcons", "dots Icons", "dots Icons"
f.em, f.ascent, f.descent = EM, ASCENT, DESCENT
f.encoding = "UnicodeFull"
f.copyright = "Phosphor Icons (MIT); Simple Icons (CC0)"

TMP = tempfile.mkdtemp()

def wrap(body, w, h, name):
    """Rewrite an icon onto the shared CANVAS, centred, preserving its aspect."""
    k = CANVAS / max(w, h)
    dx, dy = (CANVAS - w * k) / 2.0, (CANVAS - h * k) / 2.0
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS:.0f} {CANVAS:.0f}" '
           f'width="{CANVAS:.0f}" height="{CANVAS:.0f}">'
           f'<g transform="translate({dx:.4f},{dy:.4f}) scale({k:.6f})">{body}</g></svg>')
    p = os.path.join(TMP, f"{name}.svg")
    open(p, "w", encoding="utf-8").write(svg)
    return p

def source_svg(name, src, icon, weight):
    if src == "ph":
        rel = f"assets/regular/{icon}.svg" if weight == "regular" \
              else f"assets/{weight}/{icon}-{weight}.svg"
        raw = open(f"{PH_SRC}/{rel}", encoding="utf-8").read()
        if weight == "duotone":
            # a font glyph has no opacity, so the opacity="0.2" backing shape would
            # import as a second SOLID path and bury the icon
            raw = re.sub(r'<path[^>]*opacity="0?\.\d+"[^>]*/>', "", raw)
        body = raw[raw.index(">", raw.index("<svg")) + 1: raw.rindex("</svg>")]
        return wrap(body, 256.0, 256.0, name)
    d = ICONIFY[src]
    ic = d["icons"][icon]
    return wrap(ic["body"], float(ic.get("width", d.get("width", 16))),
                float(ic.get("height", d.get("height", 16))), name)

# --- probe: measure how fontforge scales the shared canvas, once -------------------
probe = f.createChar(0xE000, "dotsProbe")
probe.importOutlines(wrap('<rect x="0" y="0" width="100" height="100"/>',
                          100.0, 100.0, "dotsProbe"), scale=False, correctdir=True)
pb = probe.boundingBox()
canvas_units = max(pb[2] - pb[0], pb[3] - pb[1])      # CANVAS in fontforge units
if canvas_units <= 0:
    raise SystemExit("probe imported empty; cannot determine import scale")
SCALE = BOX / canvas_units
ox, oy = pb[0], pb[1]                                  # canvas origin in those units
f.removeGlyph(probe)

# where the canvas lands once scaled: centre it in the cell, on the cap band
TX = (ADVANCE - BOX) / 2.0 - ox * SCALE
TY = (CAP - BOX) / 2.0 - oy * SCALE

rows = []
for line in open(REG, encoding="utf-8"):
    line = line.rstrip("\n")
    if line.strip() and not line.startswith("#"):
        n, src, icon, w, cp = line.split("\t")
        rows.append((n, src, icon, w, int(cp, 16)))

for name, src, icon, weight, cp in rows:
    g = f.createChar(cp, name)
    g.importOutlines(source_svg(name, src, icon, weight), scale=False, correctdir=True)
    g.transform(psMat.compose(psMat.scale(SCALE), psMat.translate(TX, TY)))
    g.width = ADVANCE
    g.round()

f.generate(OUT)
with open(OUT + ".manifest.tsv", "w", encoding="utf-8") as m:
    for name, src, icon, weight, cp in rows:
        m.write(f"{name}\t{src}\t{icon}\t{weight}\tU+{cp:X}\t{chr(cp)}\n")
print(f"dots-icons: {len(rows)} glyphs from {1 + len(ICONIFY)} sets, "
      f"canvas {canvas_units:.0f}u -> {BOX}, advance {ADVANCE}")

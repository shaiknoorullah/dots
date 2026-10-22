"""Regenerate codepoints.tsv from every icon source.

    python3 gen-codepoints.py codepoints.tsv <phosphor-src> <iconify-icons.json>...

Columns: glyph-name<TAB>source<TAB>icon-name<TAB>weight<TAB>codepoint(hex)

The registry is COMMITTED and APPEND-ONLY. An existing glyph-name keeps its codepoint
forever; only genuinely new icons are appended. Reassigning would silently change every
config that already references a character -- the one failure mode a diff cannot show.

Glyph names are prefixed per source so the sets can never collide:
  ph   -> <icon>[-<weight>]   Phosphor, 6 weights, from a checkout of assets/<weight>/
  si   -> si-<icon>           Simple Icons, monochrome brand marks
  dev  -> dev-<icon>          Devicon Plain, monochrome language/tool marks
  vsc  -> vsc-<icon>          Codicons, editor and VCS UI

Only MONOCHROME sets are included. Multicolour sets (SVG Logos, the coloured Devicon) are
excluded deliberately: a font glyph carries no colour, so they would flatten into blobs,
and in the GTK theme they would fight dark mode instead of inheriting from it.
"""
import json, pathlib, sys

WEIGHTS = ["thin", "light", "regular", "bold", "fill", "duotone"]
BASE, LAST = 0x100000, 0x10FFFD
PREFIX = {"simple-icons": "si", "devicon-plain": "dev", "codicon": "vsc"}

out = pathlib.Path(sys.argv[1])
ph_src = pathlib.Path(sys.argv[2])
bundles = [pathlib.Path(a) for a in sys.argv[3:]]

have = {}
for w in WEIGHTS:
    d = ph_src / "assets" / w
    if not d.is_dir():
        continue
    for f in sorted(d.glob("*.svg")):
        icon = f.stem[: -len(f"-{w}")] if w != "regular" else f.stem
        have[icon if w == "regular" else f"{icon}-{w}"] = ("ph", icon, w)

for b in bundles:
    data = json.loads(b.read_text(encoding="utf-8"))
    pre = PREFIX[data["prefix"]]
    for icon in sorted(data["icons"]):
        have[f"{pre}-{icon}"] = (data["prefix"], icon, "-")

existing, used, header = {}, set(), []
if out.exists():
    for ln in out.read_text(encoding="utf-8").split("\n"):
        if ln.startswith("#") or not ln.strip():
            header.append(ln); continue
        parts = ln.split("\t")
        n, cp = parts[0], parts[-1]          # 4-column rows predate multi-source
        existing[n] = int(cp, 16); used.add(int(cp, 16))

nxt = BASE
def alloc():
    global nxt
    while nxt in used:
        nxt += 1
    if nxt > LAST:
        raise SystemExit("plane 16 exhausted")
    used.add(nxt)
    return nxt

rows, added = [], 0
for n in sorted(have):
    src, icon, w = have[n]
    rows.append((n, src, icon, w, existing[n] if n in existing else alloc()))
    added += n not in existing

rows.sort(key=lambda r: r[4])
out.write_text("\n".join(header).rstrip("\n") + "\n" +
               "\n".join(f"{n}\t{s}\t{i}\t{w}\t{cp:X}" for n, s, i, w, cp in rows) + "\n",
               encoding="utf-8")
gone = sorted(set(existing) - set(have))
print(f"{len(rows)} glyphs ({added} newly assigned, {len(gone)} no longer upstream)")

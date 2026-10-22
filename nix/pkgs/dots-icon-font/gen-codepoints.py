"""Regenerate codepoints.tsv from an upstream Phosphor checkout.

    python3 gen-codepoints.py <phosphor-src> codepoints.tsv

The registry is COMMITTED and APPEND-ONLY. Existing name->codepoint pairs are preserved
exactly; only genuinely new upstream icons are appended at the end. Reassigning a codepoint
would silently change every config that already references it, so this never sorts in place.
"""
import pathlib, sys

WEIGHTS = ["thin", "light", "regular", "bold", "fill", "duotone"]
BASE, LAST = 0x100000, 0x10FFFD

src, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

def glyph_name(ph, w):
    return ph if w == "regular" else f"{ph}-{w}"

# upstream inventory
have = {}
for w in WEIGHTS:
    for f in sorted((src / "assets" / w).glob("*.svg")):
        ph = f.stem[: -len(f"-{w}")] if w != "regular" else f.stem
        have[glyph_name(ph, w)] = (ph, w)

# keep whatever the committed registry already assigned
existing, used, header = {}, set(), []
if out.exists():
    for ln in out.read_text(encoding="utf-8").split("\n"):
        if ln.startswith("#") or not ln.strip():
            header.append(ln)
            continue
        n, ph, w, cp = ln.split("\t")
        existing[n] = (ph, w, int(cp, 16)); used.add(int(cp, 16))

nxt = BASE
def alloc():
    global nxt
    while nxt in used:
        nxt += 1
    if nxt > LAST:
        raise SystemExit("plane 16 exhausted")
    used.add(nxt); return nxt

rows, added = [], 0
for n in sorted(have, key=lambda k: (have[k][0], WEIGHTS.index(have[k][1]))):
    ph, w = have[n]
    if n in existing:
        rows.append((n, ph, w, existing[n][2]))
    else:
        rows.append((n, ph, w, alloc())); added += 1

dropped = [n for n in existing if n not in have]
rows.sort(key=lambda r: r[3])
out.write_text("\n".join(header).rstrip("\n") + "\n" +
               "\n".join(f"{n}\t{ph}\t{w}\t{cp:X}" for n, ph, w, cp in rows) + "\n",
               encoding="utf-8")
print(f"{len(rows)} glyphs ({added} newly assigned, {len(dropped)} gone upstream)")
if dropped:
    print("  upstream dropped:", ", ".join(sorted(dropped)[:10]))

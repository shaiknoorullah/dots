#!/usr/bin/env bash
# dots-theme: both producers emit a schema-valid tokens.json with the SAME key set,
# every derived tone matches an independent derivation, and emit writes its consumers.
# Offline: matugen is a stub on PATH, nothing here touches the real ~/.local/state.
set -uo pipefail
root="$(git rev-parse --show-toplevel)"
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
bash "$root/tests/render.sh" "$root/tests/data-nvidia-tmux.toml" "$out" || { echo "FAIL: render"; exit 1; }
bin="$out/.local/bin/dots-theme"
[ -x "$bin" ] || { echo "FAIL: dots-theme not rendered executable"; exit 1; }

# a stub matugen printing the shape matugen 4.x gives for `image --json hex`
# (checked against the real binary: colors.<role>.dark.color)
stub="$out/stub"; mkdir -p "$stub"
cat > "$stub/matugen" <<'EOF'
#!/bin/sh
cat <<'JSON'
{"colors":{
"background":{"dark":{"color":"#10141a"},"default":{"color":"#10141a"},"light":{"color":"#f9f9ff"}},
"surface":{"dark":{"color":"#10141a"}},
"surface_dim":{"dark":{"color":"#10141a"}},
"surface_container":{"dark":{"color":"#1c2026"}},
"surface_container_high":{"dark":{"color":"#262a31"}},
"outline":{"dark":{"color":"#8a919e"}},
"outline_variant":{"dark":{"color":"#414751"}},
"on_surface":{"dark":{"color":"#dfe2eb"}},
"on_surface_variant":{"dark":{"color":"#c0c7d4"}},
"primary":{"dark":{"color":"#a2c9ff"}},
"on_primary":{"dark":{"color":"#00315b"}}}}
JSON
EOF
chmod +x "$stub/matugen"
: > "$out/wall.png"

real_matugen="$(command -v matugen || true)"
export HOME="$out" XDG_STATE_HOME="$out/state" XDG_CONFIG_HOME="$out/cfgout"
export PATH="$stub:$PATH"
mkdir -p "$XDG_CONFIG_HOME"

python3 - "$root" "$bin" "$out" <<'PY'
import json, os, re, subprocess, sys
root, bin_, out = sys.argv[1:4]
import jsonschema
schema = json.load(open(f"{root}/docs/contracts/tokens.schema.json"))
tok = f"{out}/state/dots-theme/tokens.json"
fails = []
def check(name, ok, extra=""):
    if not ok: fails.append(f"{name} {extra}".strip())

def run(*a):
    r = subprocess.run([bin_, *a], capture_output=True, text=True)
    check(f"dots-theme {' '.join(a)} exit 0", r.returncode == 0, r.stderr.strip())
    return r

def keys(d, p=""):
    for k, v in d.items():
        if isinstance(v, dict): yield from keys(v, f"{p}{k}.")
        else: yield p + k

# palette, parsed independently of the template
pal = {}
for line in open(f"{root}/home/.chezmoidata/palette.yaml"):
    m = re.match(r'^  (bg|fg|sel|comment|cyan|green|orange|pink|purple|red|yellow): "(#[0-9a-f]{6})"', line)
    if m: pal[m.group(1)] = m.group(2)
check("palette parsed", len(pal) == 11)

def rgb(h): return [int(h[i:i+2], 16) for i in (1, 3, 5)]
def hx(c): return "#" + "".join(f"{round(v):02x}" for v in c)
def mix(a, b, t): return hx([x + (y - x) * t for x, y in zip(rgb(a), rgb(b))])

run("preset")
preset = json.load(open(tok))
jsonschema.validate(preset, schema)
c = preset["color"]
check("source preset", preset["source"] == "preset" and preset["wallpaper"] is None)
want = {
    "bg": pal["bg"], "surface": mix(pal["bg"], "#ffffff", .04), "surfaceDim": mix(pal["bg"], "#000000", .20),
    "surfaceContainer": pal["sel"], "surfaceContainerHigh": mix(pal["sel"], "#ffffff", .06),
    "outline": pal["comment"], "outlineVariant": mix(pal["comment"], pal["bg"], .5),
    "fg": pal["fg"], "fgDim": pal["comment"], "fgFaint": mix(pal["bg"], pal["comment"], .55),
    "accent": pal["purple"], "accentFg": pal["bg"], "amber": pal["orange"], "red": pal["red"],
}
for k, v in want.items(): check(f"preset derivation {k}", c[k] == v, f"got {c[k]} want {v}")

# determinism: a second run is byte-identical
a = open(tok).read(); run("preset"); check("preset is deterministic", open(tok).read() == a)

run("wallpaper", f"{out}/wall.png")
wall = json.load(open(tok))
jsonschema.validate(wall, schema)
check("source wallpaper", wall["source"] == "wallpaper" and wall["wallpaper"] == f"{out}/wall.png")
check("same key set in both modes", sorted(keys(preset)) == sorted(keys(wall)))
check("wallpaper accent comes from matugen primary", wall["color"]["accent"] == "#a2c9ff")
for k in ("amber", "red", "green"):
    check(f"status colour {k} stays semantic in wallpaper mode", wall["color"][k] == preset["color"][k])

# a wallpaper that matugen cannot map must fail loudly and leave tokens.json intact
before = open(tok).read()
r = subprocess.run([bin_, "wallpaper", f"{out}/missing.png"], capture_output=True, text=True)
check("missing wallpaper exits non-zero", r.returncode != 0)
check("failed run leaves tokens.json intact", open(tok).read() == before)

# emit regenerates the consumers from tokens.json
for p in ("hypr/colors.conf", "ghostty/dots-theme.conf", "swaync/dots-theme.css", "hypr/hyprlock-colors.conf"):
    f = f"{out}/cfgout/{p}"
    check(f"emit wrote {p}", os.path.isfile(f))
check("colors.conf carries the accent", "rgb(a2c9ff)" in open(f"{out}/cfgout/hypr/colors.conf").read())
os.remove(f"{out}/cfgout/hypr/colors.conf"); run("emit")
check("emit alone rebuilds colors.conf", os.path.isfile(f"{out}/cfgout/hypr/colors.conf"))
leftover = [n for n in os.listdir(f"{out}/state/dots-theme") if n.endswith(".tmp")]
check("no .tmp files left behind", not leftover, str(leftover))

if fails:
    print("\n".join("FAIL: " + f for f in fails)); sys.exit(1)
print("theme ok")
PY
rc=$?
[ "$rc" = 0 ] || exit "$rc"

# when a real matugen is installed, prove the role mapping against its actual output too
if [ -n "$real_matugen" ] && command -v magick >/dev/null; then
  magick -size 64x64 'gradient:#336699-#aa4466' "$out/real.png"
  PATH="$(dirname "$real_matugen"):$PATH" "$bin" wallpaper "$out/real.png" || { echo "FAIL: real matugen run"; exit 1; }
  jq -e '.source == "wallpaper" and (.color.accent | test("^#[0-9a-f]{6}$"))' "$XDG_STATE_HOME/dots-theme/tokens.json" >/dev/null \
    || { echo "FAIL: real matugen tokens"; exit 1; }
  echo "theme ok (real matugen)"
fi

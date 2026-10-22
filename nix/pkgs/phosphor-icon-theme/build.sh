#!/usr/bin/env bash
# Generate a freedesktop icon theme from the Phosphor SVG assets.
#
# Phosphor is a UI icon set, not an icon theme: its files are assets/<weight>/<name>.svg
# with Phosphor's own names. A desktop needs freedesktop names (folder, edit-find,
# go-previous) in context directories under an index.theme. mapping.tsv is that bridge.
#
# Two emissions per row:
#   scalable/<ctx>/<fd>.svg           the row's weight, fill baked to $FG
#   symbolic/<ctx>/<fd>-symbolic.svg  the SAME weight; many apps request -symbolic by name,
#                                     and mixing outline chrome into a solid set looks wrong
#
# Phosphor ships fill="currentColor". librsvg resolves that to BLACK when no colour is
# inherited, which is invisible on a dark desktop -- so a concrete colour is substituted.
# Symbolic icons get a dark placeholder because GTK recolours those itself.
set -euo pipefail
src=$1 map=$2 out=$3 theme=$4
FG='#fcfcfc'          # breeze-dark ColorScheme-Text
SYM='#2e3436'         # placeholder; GTK overrides it for *-symbolic

root="$out/share/icons/$theme"
mkdir -p "$root"
declare -A ctxseen=()
n=0 missing=0

while IFS=$'\t' read -r ctx fd ph weight; do
  case "${ctx:-}" in ''|'#'*) continue ;; esac
  [ -n "${weight:-}" ] || continue
  if [ "$weight" = regular ]; then rel="regular/$ph.svg"; else rel="$weight/$ph-$weight.svg"; fi
  if [ ! -f "$src/assets/$rel" ]; then
    echo "missing upstream asset: $rel (for $fd)" >&2; missing=$((missing+1)); continue
  fi
  mkdir -p "$root/scalable/$ctx" "$root/symbolic/$ctx"
  sed "s|currentColor|$FG|g"  "$src/assets/$rel" > "$root/scalable/$ctx/$fd.svg"
  sed "s|currentColor|$SYM|g" "$src/assets/$rel" > "$root/symbolic/$ctx/$fd-symbolic.svg"
  ctxseen[$ctx]=1
  n=$((n+1))
done < "$map"

[ "$missing" = 0 ] || { echo "$missing mapping row(s) had no upstream asset" >&2; exit 1; }
[ "$n" -gt 0 ]     || { echo "mapping produced no icons" >&2; exit 1; }

# index.theme. Inherits=breeze-dark so every unmapped name -- app logos above all --
# resolves exactly as it did before this theme existed; hicolor is the spec's last resort.
{
  printf '[Icon Theme]\nName=%s\nComment=Phosphor icons mapped onto freedesktop names\n' "$theme"
  printf 'Inherits=breeze-dark,hicolor\n'
  # Both go in Directories. ScaledDirectories is for HiDPI variants of the SAME directory,
  # not for symbolic ones -- a symbolic dir listed there is never searched.
  dirs=()
  for c in "${!ctxseen[@]}"; do dirs+=("scalable/$c"); done
  for c in "${!ctxseen[@]}"; do dirs+=("symbolic/$c"); done
  printf 'Directories=%s\n\n' "$(IFS=,; echo "${dirs[*]}")"
  for c in "${!ctxseen[@]}"; do
    # Context must be the spec's capitalised form, not our lowercase directory name.
    case "$c" in
      places)     C=Places ;;      devices)    C=Devices ;;
      actions)    C=Actions ;;      status)     C=Status ;;
      mimetypes)  C=MimeTypes ;;    categories) C=Categories ;;
      *)          C=Applications ;;
    esac
    printf '[scalable/%s]\nSize=48\nMinSize=8\nMaxSize=512\nContext=%s\nType=Scalable\n\n' "$c" "$C"
    printf '[symbolic/%s]\nSize=16\nMinSize=8\nMaxSize=512\nContext=%s\nType=Scalable\n\n' "$c" "$C"
  done
} > "$root/index.theme"

echo "$theme: $n icons across ${#ctxseen[@]} contexts"

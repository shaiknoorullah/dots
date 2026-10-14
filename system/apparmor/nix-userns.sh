#!/usr/bin/env bash
# Run as root: nix-userns.sh NAME=/nix/store/<hash>-<pkg>/<path> ...
#
# Nix-built Chromium/Electron apps cannot use Chromium's setuid sandbox helper (the Nix store holds no setuid
# files), and on Ubuntu 23.10+ their namespace-sandbox fallback is blocked by
# kernel.apparmor_restrict_unprivileged_userns unless an AppArmor profile grants `userns`. Each NAME=PATH gets
# such a profile (unconfined: it only names the app and grants userns, like Ubuntu's own /etc/apparmor.d/brave).
#
# Exact paths only, never a /nix/store/* glob: any local user can add arbitrary paths to the Nix store through
# the daemon, so a glob would hand userns to anything shaped like the app and defeat the restriction. The caller
# takes the paths from the user's own home-manager closure; this script re-checks each one.
#
# Files are /etc/apparmor.d/dots-nix-NAME. Managed files no longer passed in are unloaded and removed, as are
# the earlier wildcard profiles (slack-nix, brave-nix) when they still contain a /nix/store/* glob.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "nix-userns: run as root" >&2; exit 1; }
dir=/etc/apparmor.d
marker='# managed by dots: system/apparmor/nix-userns.sh'
declare -A want=()

for arg in "$@"; do
  name=${arg%%=*} path=${arg#*=}
  [[ $name =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || { echo "nix-userns: bad name '$name'" >&2; exit 1; }
  [[ $path =~ ^/nix/store/[0-9a-z]{32}-[A-Za-z0-9+._?=-]+(/[A-Za-z0-9+._-]+)+$ ]] || { echo "nix-userns: not a plain store path: $path" >&2; exit 1; }
  [ -f "$path" ] && [ "$(readlink -f "$path")" = "$path" ] || { echo "nix-userns: not a regular file reached without symlinks: $path" >&2; exit 1; }
  [ "$(stat -c %u "$path")" = 0 ] || { echo "nix-userns: not root-owned: $path" >&2; exit 1; }
  (( 8#$(stat -c %a "$path") & 8#022 )) && { echo "nix-userns: group/other-writable: $path" >&2; exit 1; }
  want[$name]=$path
done

for name in "${!want[@]}"; do
  f="$dir/dots-nix-$name"
  printf '%s\n' "$marker" "abi <abi/4.0>," "include <tunables/global>" "" \
    "profile dots-nix-$name \"${want[$name]}\" flags=(unconfined) {" "  userns," "}" > "$f.new"
  chmod 644 "$f.new"; mv -f "$f.new" "$f"
  apparmor_parser -r "$f"
  echo "apparmor: dots-nix-$name -> ${want[$name]}"
done

# drop managed profiles that are no longer wanted, and the old wildcard ones
for f in "$dir"/dots-nix-* "$dir"/slack-nix "$dir"/brave-nix; do
  [ -f "$f" ] || continue
  n=${f##*/}
  if [[ $n == dots-nix-* ]]; then
    grep -qxF "$marker" "$f" && [ -z "${want[${n#dots-nix-}]+x}" ] || continue
  else
    grep -q '/nix/store/\*' "$f" || continue
  fi
  apparmor_parser -R "$f" 2>/dev/null || true
  rm -f "$f"
  echo "apparmor: removed $n"
done

#!/usr/bin/env bash
set -uo pipefail
root="$(git rev-parse --show-toplevel)"; out="$(mktemp -d)"
bash "$root/tests/render.sh" "$root/tests/data-nvidia-tmux.toml" "$out" || { echo "LINT: render failed"; exit 1; }
pat='/home/devsupreme|/home/linuxbrew|/snap/|\.cargo/bin|/run/user/1001|/usr/bin/(task|timew|python3|kubectl|tmux|gh|kitty|swww)\b|kitty --|kitten|\.config/kitty|\bkitty\b *$|"kitty"|mako|\brofi\b|oh-my-zsh'
if grep -rIlE "$pat" "$out"; then echo "LINT: files above contain forbidden patterns"; exit 1; fi
grep -q "^keybind = clear" "$out/.config/ghostty/config" || { echo "LINT: ghostty config lacks keybind = clear"; exit 1; }
# non-NixOS PATH keeps the sbin dirs; NixOS PATH has the system profile + setuid wrappers (no /usr on NixOS)
for f in .config/environment.d/10-dots-path.conf .config/hypr/hyprland.lua; do
  grep -q '/usr/sbin' "$out/$f" || { echo "LINT: $f (nvidia render) PATH lacks /usr/sbin"; exit 1; }
done
# public repo: company/project identifiers live in the hand-copied ~/.config/pi-fleet/config.sh, not in the scripts (I2)
for f in .local/bin/pi-fleet-monitor .local/bin/pnats-agent-browser .local/bin/pi-watch; do
  [ -f "$out/$f" ] || { echo "LINT: $f not rendered"; exit 1; }
  if grep -iE 'pnow|kaarbaaz|code42|proficient|websleak|contabo|qwen36|litellm/' "$out/$f"; then echo "LINT: $f names a company/project (move it to pi-fleet/config.sh)"; exit 1; fi
  grep -q 'pi-fleet/config.sh' "$out/$f" || [ "$f" = .local/bin/pi-watch ] || { echo "LINT: $f does not read pi-fleet/config.sh"; exit 1; }
done
if grep -v '^[[:space:]]*#' "$out/.local/bin/pnats-agent-browser" | grep -q -- '--no-sandbox'; then echo "LINT: pnats-agent-browser defaults to --no-sandbox (must be opt-in via config)"; exit 1; fi
# public repo: the kube tunnel (conf, unit, helper) is copied by hand, never rendered (docs/install.md §2)
if find "$out" \( -name '*kube-tunnel*' -o -name '*k8s-tunnel*' \) -print | grep -q .; then
  echo "LINT: a kube tunnel target is rendered; it must be copied by hand"; exit 1
fi
# the ssh key menu discovers keys from ~/.ssh (labels come from the hand-copied ssh-keys.conf), so it names nothing
if grep -iE 'pnow|kaarbaaz|code42|proficient|websleak|contabo|hostinger|aws-hg' "$out/.config/dots-mux/ssh-menu.sh"; then
  echo "LINT: ssh-menu.sh names a company/host (it must discover keys from ~/.ssh)"; exit 1
fi
nixos="$(mktemp -d)"
bash "$root/tests/render.sh" "$root/tests/data-nixos-tmux.toml" "$nixos" || { echo "LINT: nixos render failed"; exit 1; }
if grep -rIlE "$pat" "$nixos"; then echo "LINT: files above (nixos render) contain forbidden patterns"; exit 1; fi
for f in .config/environment.d/10-dots-path.conf .config/hypr/hyprland.lua; do
  grep -q '/run/current-system/sw/bin' "$nixos/$f" || { echo "LINT: $f (nixos render) PATH lacks /run/current-system/sw/bin"; exit 1; }
  grep -q '/run/wrappers/bin' "$nixos/$f" || { echo "LINT: $f (nixos render) PATH lacks /run/wrappers/bin"; exit 1; }
done
# R-pub: no rendered target may carry a private address — this repo is public and names no host or cluster
for d in "$out" "$nixos"; do
  if grep -rIlE '(^|[^0-9.])(10\.[0-9]{1,3}|192\.168|172\.(1[6-9]|2[0-9]|3[01])\.)[0-9]{1,3}\.[0-9]{1,3}' "$d"; then
    echo "LINT: files above carry a private IP (this repo is public)"; exit 1
  fi
done
# R28: everything uses ~/.nix-profile, NixOS included (home-manager.useUserPackages = false)
if grep -rIl '/etc/profiles/per-user' "$out" "$nixos"; then echo "LINT: files above use /etc/profiles/per-user (R28: use ~/.nix-profile)"; exit 1; fi
echo "lint ok"

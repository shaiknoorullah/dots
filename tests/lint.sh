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
# R-glyph: no Nerd Font icon glyphs. They are private-use codepoints, so they render as
# tofu in any font that is not patched, they cannot be grepped or read aloud, and they look
# nothing like the icon theme. Replaced by geometric Unicode from normal fonts.
# Two exceptions. U+E0B0-U+E0D4 is the Powerline separator block, which draws starship's
# segment shape rather than standing in for an icon. Plane 16 (U+100000+) is OUR font,
# dots-icon-font, whose glyphs are Phosphor outlines -- Nerd Font never uses plane 16, so
# allowing it cannot re-admit a single glyph this rule exists to keep out.
for d in "$out" "$nixos"; do
  if LC_ALL=C.UTF-8 grep -rIlP '[\x{E000}-\x{E0AF}\x{E0D5}-\x{F8FF}\x{F0000}-\x{FFFFD}]' "$d"; then
    echo "LINT: files above use Nerd Font glyphs (banned; only U+E0B0-E0D4 separators are allowed)"; exit 1
  fi
  # ...and the same glyphs written as shell ESCAPES. $'\xef\x80\x84' and $'\U000f0343' are
  # U+F004 and U+F0343, but the file is pure ASCII, so the codepoint grep above sees nothing
  # while the terminal happily paints a private-use glyph. Both forms were live in
  # otter-launcher and survived the purge undetected.
  if grep -rIlP '\\U000[eEfF][0-9a-fA-F]{4}' "$d"; then
    echo "LINT: files above encode a Nerd Font glyph as a \\U escape"; exit 1
  fi
  if grep -rIlP '\\x[eE][eEfF]\\x[89aAbB][0-9a-fA-F]\\x[89aAbB][0-9a-fA-F]' "$d"; then
    echo "LINT: files above encode a private-use glyph as UTF-8 \\x escapes"; exit 1
  fi
done

# R-deadman: layer 2 of the dead-man's switch must stay IMPORT-FREE.
# timetrack-deadman exists for the case where a blocking panel with a compositor-enforced
# keyboard grab has wedged, and the likeliest cause of a wedge is a bug in the Python that
# drew it. A reaper that called into that Python would die of the same bug and leave the
# machine unusable. The independence is the feature, so it is checked mechanically rather
# than left as a comment someone later "tidies up".
dm="$out/.local/bin/timetrack-deadman"
[ -f "$dm" ] || { echo "LINT: timetrack-deadman is not rendered"; exit 1; }
# comments are stripped first: the script's own header SAYS "no python", which is the
# documentation of the rule, not a violation of it
if grep -v '^[[:space:]]*#' "$dm" | grep -nE '(^|[^-[:alnum:]_/])python'; then
  echo "LINT: timetrack-deadman invokes python (layer 2 must import nothing)"; exit 1
fi
if grep -v '^[[:space:]]*#' "$dm" | grep -nE '(^|[^-[:alnum:]_/.])timetrack +[a-z]'; then
  echo "LINT: timetrack-deadman calls the timetrack CLI (layer 2 must import nothing)"; exit 1
fi
# R-escape: layer 4 is the escape route printed ON the panel. A modal that grabs the
# keyboard and does not say how to get out of it is the failure mode, not a detail.
pn="$out/.local/bin/timetrack-panel"
[ -f "$pn" ] || { echo "LINT: timetrack-panel is not rendered"; exit 1; }
for esc in 'timetrack/PANIC' 'hyprctl reload' 'systemctl --user stop timetrack-panel'; do
  grep -qF "$esc" "$pn" || { echo "LINT: timetrack-panel does not print the escape '$esc'"; exit 1; }
done
grep -qF 'timetrack-deadman.timer' "$pn" || {
  echo "LINT: timetrack-panel does not require the reaper before drawing"; exit 1; }
# R-grab: dots.block is the ONLY window allowed to hold the keyboard. stay_focused on any
# other popup also blocks grim/slurp, so a screenshot of it becomes impossible -- that is
# why it was removed from the whole dots.* family, and the rule has drifted back before.
for d in "$out" "$nixos"; do
  if grep -n 'stay_focused' "$d/.config/hypr/hyprland.lua" | grep -v 'dots\[.\]block' | grep -v '^\s*[0-9]*:\s*--'; then
    echo "LINT: stay_focused on a window other than dots.block (it blocks screenshots)"; exit 1
  fi
done

# R28: everything uses ~/.nix-profile, NixOS included (home-manager.useUserPackages = false)
if grep -rIl '/etc/profiles/per-user' "$out" "$nixos"; then echo "LINT: files above use /etc/profiles/per-user (R28: use ~/.nix-profile)"; exit 1; fi
echo "lint ok"

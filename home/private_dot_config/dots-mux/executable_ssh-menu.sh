#!/usr/bin/env bash
# SSH key manager popup for tmux
# Shows all keys with load status, load/unload via fzf
#
# enter   = load key into agent (passphrase from pass)
# ctrl-u  = unload key from agent
# ctrl-a  = load ALL keys
# ctrl-d  = flush agent (unload all)
# ctrl-p  = add passphrase to unprotected key + store in pass

set -eu

# Re-launch in floating terminal if called from tmux directly
if [ -z "${TMUX_FZF_FLOAT:-}" ] && [ -n "${TMUX:-}" ]; then
    export TMUX_FZF_FLOAT=1
    exec "$HOME/.config/tmux/scripts/fzf-float.sh" "$0" "$@"
fi

# Works under tmux and Herdr: tmux status-line message when in tmux, desktop toast otherwise.
note() {
  if [ -n "${TMUX:-}" ]; then
    tmux display-message "$1"
  elif command -v notify-send >/dev/null 2>&1; then
    notify-send --app-name="dots-mux" "$1"
  else
    printf '%s\n' "$1" >&2
  fi
}

SSH_DIR="$HOME/.ssh"
ASKPASS="$HOME/.config/dots-mux/ssh-askpass.sh"

# Key registry: discovered, never hard-coded — this repo is public and names no host or company.
# An optional ~/.config/dots-mux/ssh-keys.conf (hand-copied, never in the repo) labels and orders the
# keys you care about, one "name|file|description" line each; file may be bare or absolute. Everything
# it does not mention is still discovered from ~/.ssh, so a new key appears without editing dots.
REGISTRY="$HOME/.config/dots-mux/ssh-keys.conf"
KEYS=()
listed=""
if [ -r "$REGISTRY" ]; then
    while IFS='|' read -r name file desc; do
        case "$name" in ''|\#*) continue ;; esac
        case "$file" in /*) ;; *) file="$SSH_DIR/${file:-$name}" ;; esac
        KEYS+=("$name|$file|${desc:-$name}")
        listed="$listed $file"
    done < "$REGISTRY"
fi
# then every other private key in ~/.ssh: a file with a matching .pub, plus bare .pem keys
for pub in "$SSH_DIR"/*.pub; do
    [ -e "$pub" ] || continue
    file=${pub%.pub}
    [ -f "$file" ] || continue
    case " $listed " in *" $file "*) continue ;; esac
    # a .pub carries the key's own comment; that is a better label than the filename when present
    desc=$(awk '{ $1=""; $2=""; sub(/^ */, ""); print }' "$pub" 2>/dev/null)
    KEYS+=("${file##*/}|$file|${desc:-${file##*/}}")
done
for file in "$SSH_DIR"/*.pem; do
    [ -f "$file" ] || continue
    case " $listed " in *" $file "*) continue ;; esac
    KEYS+=("${file##*/}|$file|${file##*/}")
done
[ ${#KEYS[@]} -gt 0 ] || { note "ssh-menu: no SSH keys found in $SSH_DIR"; exit 0; }

# Get fingerprints currently loaded in agent
loaded_fps=$(ssh-add -l 2>/dev/null | awk '{print $2}' || true)

# Build display list
entries=""
for entry in "${KEYS[@]}"; do
    IFS='|' read -r name file desc <<< "$entry"
    [ ! -f "$file" ] && continue

    # Get this key's fingerprint
    fp=$(ssh-keygen -lf "$file" 2>/dev/null | awk '{print $2}' || true)

    # Check if loaded
    if echo "$loaded_fps" | grep -qF "$fp" 2>/dev/null; then
        status="●"
    else
        status="○"
    fi

    # Check if key has passphrase
    if ssh-keygen -y -P "" -f "$file" &>/dev/null; then
        lock="  unlocked"
    else
        lock="  locked"
    fi

    line="$status  $name${lock}  $desc"
    if [ -z "$entries" ]; then
        entries="$line"
    else
        entries="$entries
$line"
    fi
done

if [ -z "$entries" ]; then
    note "No SSH keys found in $SSH_DIR"
    exit 0
fi

# Fuzzy pick
result=$(printf '%s\n' "$entries" | fzf \
    --prompt='  SSH Keys  ' \
    --header='enter=load  ^u=unload  ^a=load-all  ^d=flush  ^p=add-passphrase' \
    --expect='ctrl-u,ctrl-a,ctrl-d,ctrl-p' \
    --ansi \
    --no-multi \
    --layout=reverse \
    --border=rounded \
    --margin=1,2) || exit 0

[ -z "$result" ] && exit 0

# Parse fzf --expect
key=$(printf '%s' "$result" | head -1)
selection=$(printf '%s' "$result" | tail -1)

# Extract key name from selection (second field after status icon)
selected_name=$(echo "$selection" | awk '{print $2}')

# Find the file path for selected key
selected_file=""
for entry in "${KEYS[@]}"; do
    IFS='|' read -r name file desc <<< "$entry"
    if [ "$name" = "$selected_name" ]; then
        selected_file="$file"
        break
    fi
done

case "$key" in
    ctrl-a)
        # Load ALL keys
        export SSH_ASKPASS="$ASKPASS"
        export SSH_ASKPASS_REQUIRE="force"
        count=0
        for entry in "${KEYS[@]}"; do
            IFS='|' read -r name file desc <<< "$entry"
            [ ! -f "$file" ] && continue
            if ssh-add -t 3600 "$file" 2>/dev/null; then
                count=$((count + 1))
            fi
        done
        note "Loaded $count SSH keys (expire in 1h)"
        ;;

    ctrl-d)
        # Flush all keys from agent
        ssh-add -D 2>/dev/null
        note "All SSH keys unloaded from agent"
        ;;

    ctrl-u)
        # Unload selected key
        if [ -n "$selected_file" ]; then
            ssh-add -d "$selected_file" 2>/dev/null
            note "Unloaded: $selected_name"
        fi
        ;;

    ctrl-p)
        # Add passphrase to key and store in pass
        if [ -z "$selected_file" ]; then
            note "No key selected"
            exit 0
        fi

        # Check if already has passphrase
        if ! ssh-keygen -y -P "" -f "$selected_file" &>/dev/null; then
            note "$selected_name already has a passphrase"
            exit 0
        fi

        # Generate a strong passphrase
        new_pass=$(pass generate -n "ssh/$selected_name" 32 2>/dev/null | tail -1)

        if [ -z "$new_pass" ]; then
            note "Failed to generate passphrase"
            exit 1
        fi

        # Add passphrase to the key
        # ssh-keygen -p requires interactive input, so use expect-like approach
        ssh-keygen -p -f "$selected_file" -N "$new_pass" 2>/dev/null
        if [ $? -eq 0 ]; then
            note "Passphrase added to $selected_name and stored in pass (ssh/$selected_name)"
        else
            # Clean up pass entry if key encryption failed
            pass rm -f "ssh/$selected_name" 2>/dev/null
            note "Failed to add passphrase to $selected_name"
        fi
        ;;

    *)
        # Load selected key
        if [ -n "$selected_file" ]; then
            export SSH_ASKPASS="$ASKPASS"
            export SSH_ASKPASS_REQUIRE="force"
            if ssh-add -t 3600 "$selected_file" 2>/dev/null; then
                note "Loaded: $selected_name (expires in 1h)"
            else
                note "Failed to load: $selected_name"
            fi
        fi
        ;;
esac

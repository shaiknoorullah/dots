# vault-backup: hardlinked rsync snapshots of an EXPLICIT personal path list, to a LOCAL
# directory. No encryption and no password, deliberately:
#
#   - Timeshift already stores the same files unencrypted on the same disk, so encrypting
#     a second copy protected nothing while adding a key that could be lost. A backup you
#     cannot open is not a backup.
#   - A snapshot here is a plain directory tree. Restoring one note is `cp`, with no tool,
#     no repository format and nothing to remember at the moment you are least calm.
#
# Hardlinks mean each snapshot costs only what changed, so deep history is nearly free --
# which is the one thing Timeshift does not give (it keeps very few dailies).
# Work repositories are not on the list by design: company and system data is Timeshift's
# job, personal data is this one's, and the two stay separate and auditable.
job_main() {
  local root list dest prev stamp rc=0 keep
  # Default lives under a directory this user already owns: /data itself is root-owned,
  # and a backup job that needs sudo is a backup job that quietly stops running.
  root=$(ops_cfg vault.dir "/data/recovery/vault-backup")
  case $root in /*) ;; *) ops_state vault-backup fail "vault.dir must be an absolute local path"; return 0 ;; esac
  list=${XDG_CONFIG_HOME:-$HOME/.config}/dots-ops/vault-paths
  [ -r "$list" ] || { ops_state vault-backup warn "no path list at $list"; return 0; }

  local -a src=(); local p missing=0
  while IFS= read -r p || [ -n "$p" ]; do
    p=${p%%#*}; p=${p%"${p##*[![:space:]]}"}
    [ -n "$p" ] || continue
    p=${p/#\~\//$HOME/}
    if [ -e "$p" ]; then src+=("$p"); else missing=$((missing+1)); fi
  done < "$list"
  [ ${#src[@]} -gt 0 ] || { ops_state vault-backup warn "none of the listed personal paths exist yet"; return 0; }

  if ! mkdir -p "$root" 2>/dev/null; then
    ops_state vault-backup fail "cannot create $root (parent not writable). Fix with: sudo install -d -o $USER -g $USER $root"
    return 0
  fi
  [ -w "$root" ] || { ops_state vault-backup fail "$root is not writable by $USER"; return 0; }
  stamp=$(date +%Y-%m-%dT%H%M%S)
  prev=$(readlink -f "$root/latest" 2>/dev/null || true)

  local -a args=(-aR --delete --exclude=__pycache__/ --exclude=*.pyc --exclude=.venv/)
  [ -n "$prev" ] && [ -d "$prev" ] && args+=(--link-dest="$prev")
  if [ "${DOTS_OPS_DRY_RUN:-0}" = 1 ]; then
    ops_run rsync -n "${args[@]}" "${src[@]}" "$root/$stamp/" >/dev/null 2>&1 || rc=$?
    ops_state vault-backup ok "dry run"; return 0
  fi
  ops_run rsync "${args[@]}" "${src[@]}" "$root/$stamp/" >/dev/null 2>"$OPS_STATE/vault-backup.err" || rc=$?
  # 24 = "file vanished while copying", normal on a live home and not a failure
  if [ "$rc" != 0 ] && [ "$rc" != 24 ]; then
    ops_state vault-backup fail "rsync failed (rc=$rc): $(tail -n 1 "$OPS_STATE/vault-backup.err")"
    rm -rf "$root/$stamp"; return 0
  fi
  ln -sfn "$root/$stamp" "$root/latest"

  keep=$(ops_cfg vault.keep_snapshots 30)
  local -a old=()
  while IFS= read -r p; do old+=("$p"); done < <(
    find "$root" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%f\n' | sort -r | tail -n +$((keep+1)))
  local pruned=0
  for p in "${old[@]:-}"; do [ -n "$p" ] && rm -rf "${root:?}/$p" && pruned=$((pruned+1)); done

  local size
  size=$(du -sh "$root" 2>/dev/null | cut -f1)
  ops_state vault-backup ok "snapshot $stamp · ${#src[@]} paths · ${size:-?} total${pruned:+ · pruned $pruned}${missing:+ · $missing listed path(s) absent}"
}

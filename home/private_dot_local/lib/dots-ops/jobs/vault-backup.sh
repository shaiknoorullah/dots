# vault-backup: restic snapshot of an EXPLICIT personal path list, tagged `vault`.
# Deliberately never backs up work repositories: company and system data are Timeshift's
# job, personal data is this job's, and the two stay separate and auditable. The path list
# is version-controlled (~/.config/dots-ops/vault-paths) so what leaves is reviewable.
# The repository must be a LOCAL path; nothing is ever pushed off this machine.
# Deliberately NOT OPS_HEAVY: the path list is small (notes + a few SQLite files), and
# this data has no other safety net, so it must not wait for an idle window that may
# never come on a machine that is in use all day.
job_main() {
  ops_restic_env || { ops_state vault-backup warn "not configured: set RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE in ~/.secrets"; return 0; }
  case ${RESTIC_REPOSITORY:-} in
    /*|local:/*) ;;
    *) ops_state vault-backup fail "refusing to run: RESTIC_REPOSITORY is not a local path"; return 0 ;;
  esac
  local list=${XDG_CONFIG_HOME:-$HOME/.config}/dots-ops/vault-paths
  [ -r "$list" ] || { ops_state vault-backup warn "no path list at $list"; return 0; }
  local -a src=(); local p missing=0
  while IFS= read -r p || [ -n "$p" ]; do
    p=${p%%#*}                                   # strip an inline comment
    p=${p%"${p##*[![:space:]]}"}                 # and the trailing whitespace it leaves
    [ -n "$p" ] || continue
    p=${p/#\~\//$HOME/}
    if [ -e "$p" ]; then src+=("$p"); else missing=$((missing+1)); fi
  done < "$list"
  [ ${#src[@]} -gt 0 ] || { ops_state vault-backup warn "none of the listed personal paths exist yet"; return 0; }
  local out err rc=0 line sid size dry=${DOTS_OPS_DRY_RUN:-0}
  err=$OPS_STATE/vault-backup.err
  out=$(ops_run restic backup "${src[@]}" --tag vault --exclude-caches --json 2>"$err") || rc=$?
  [ "$dry" != 1 ] || printf '%s\n' "$out"
  line=$(tail -n 1 "$err")
  if [ "$rc" = 11 ]; then ops_state vault-backup warn "repository locked: $line"; return 0; fi
  if [ "$rc" != 0 ] && [ "$rc" != 3 ]; then ops_state vault-backup fail "restic backup failed (rc=$rc): $line"; return 0; fi
  sid=$(printf '%s\n' "$out" | jq -rs 'map(select(.message_type? == "summary")) | last | .snapshot_id // empty | .[0:8]' 2>/dev/null)
  size=$(printf '%s\n' "$out" | jq -rs 'map(select(.message_type? == "summary")) | last | .data_added // empty' 2>/dev/null)
  rc=0
  out=$(ops_run restic forget --tag vault --keep-daily "$(ops_cfg vault.keep_daily 14)" \
        --keep-weekly "$(ops_cfg vault.keep_weekly 8)" --keep-monthly "$(ops_cfg vault.keep_monthly 12)" --prune 2>"$err") || rc=$?
  [ "$dry" != 1 ] || printf '%s\n' "$out"
  if [ "$rc" = 11 ]; then ops_state vault-backup warn "repository locked during forget: $(tail -n 1 "$err")"; return 0; fi
  if [ "$rc" != 0 ]; then ops_state vault-backup fail "restic forget failed (rc=$rc): $(tail -n 1 "$err")"; return 0; fi
  if [ "$dry" = 1 ]; then ops_state vault-backup ok "dry run"; return 0; fi
  ops_state vault-backup ok "snapshot ${sid:-?} · ${#src[@]} paths${size:+ · +$((size/1024/1024))MiB}${missing:+ · $missing listed path(s) absent}"
}

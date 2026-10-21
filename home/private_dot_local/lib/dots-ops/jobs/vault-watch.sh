# vault-watch: fail when the newest `vault` snapshot is older than vault.max_age_hours.
# Personal data has no other safety net on this machine (company IT backups do not cover
# ~/powerhouse), so a stale vault snapshot is a failure, not a warning.
job_main() {
  ops_restic_env || { ops_state vault-watch ok "n/a: backups not configured"; return 0; }
  local out err rc=0 max t ts newest=0 age
  err=$OPS_STATE/vault-watch.err
  out=$(ops_run restic snapshots --tag vault --latest 1 --json 2>"$err") || rc=$?
  if [ "$rc" != 0 ]; then ops_state vault-watch warn "cannot read the repository (rc=$rc): $(tail -n 1 "$err")"; return 0; fi
  max=$(ops_cfg vault.max_age_hours 36)
  while read -r t; do
    ts=$(date -d "$t" +%s 2>/dev/null) || continue
    [ "$ts" -le "$newest" ] || newest=$ts
  done < <(printf '%s\n' "$out" | jq -r '.[]?.time // empty' 2>/dev/null)
  if [ "$newest" = 0 ]; then ops_state vault-watch fail "no vault snapshot in the repository"; return 0; fi
  age=$(( ($(ops_now) - newest) / 3600 ))
  if [ "$age" -gt "$max" ]; then ops_state vault-watch fail "last vault backup ${age}h ago (max ${max}h)"
  else ops_state vault-watch ok "last vault backup ${age}h ago"; fi
}

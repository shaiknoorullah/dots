# vault-watch: fail when the newest personal snapshot is older than vault.max_age_hours.
# A failure, not a warning: company IT backups do not cover ~/powerhouse, so a stale
# snapshot means this data has no copy at all.
job_main() {
  local root newest age max
  root=$(ops_cfg vault.dir "/data/recovery/vault-backup")
  [ -d "$root" ] || { ops_state vault-watch fail "no vault backup directory at $root"; return 0; }
  newest=$(find "$root" -mindepth 1 -maxdepth 1 -type d -name '20*' -printf '%T@ %f\n' 2>/dev/null | sort -rn | head -1)
  [ -n "$newest" ] || { ops_state vault-watch fail "no snapshot in $root"; return 0; }
  age=$(( ($(ops_now) - ${newest%%.*}) / 3600 ))
  max=$(ops_cfg vault.max_age_hours 36)
  if [ "$age" -gt "$max" ]; then ops_state vault-watch fail "last vault snapshot ${age}h ago (max ${max}h)"
  else ops_state vault-watch ok "last vault snapshot ${age}h ago (${newest#* })"; fi
}

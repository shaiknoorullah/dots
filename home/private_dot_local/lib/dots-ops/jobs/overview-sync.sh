# overview-sync: hourly. The slow half of dots-overview -- GitHub + ERP read-only, then the
# completion detectors, then the invariants. It skips quietly when offline. It only ever
# READS the sources: completion is marked in local Taskwarrior and nowhere else.
# A failed source or a broken invariant turns the ops module amber instead of failing
# silently; the exit code of `doctor` is the whole signal.
job_main() {
  local out rc=0 sum
  command -v dots-overview >/dev/null 2>&1 || { ops_state overview-sync warn "dots-overview is not installed"; return 0; }
  if ! timeout 5 getent hosts api.github.com >/dev/null 2>&1; then
    ops_state overview-sync ok "offline; skipped"; return 0
  fi
  out=$(timeout 600 dots-overview sync 2>&1) || rc=$?
  timeout 600 dots-overview detect >/dev/null 2>&1 || rc=$((rc + 1))
  timeout 120 dots-overview fast --apps all >/dev/null 2>&1 || rc=$((rc + 1))
  sum=$(dots-overview doctor 2>&1 | tail -n 1)
  if [ "$rc" -ne 0 ]; then ops_state overview-sync warn "${out##*$'\n'}; $sum"
  elif dots-overview doctor >/dev/null 2>&1; then ops_state overview-sync ok "${out##*$'\n'}"
  else ops_state overview-sync warn "$sum"; fi
  return 0
}

# timetrack-capture: keep the work-time record current without being asked.
#
# This exists because the live clock does not yet. Until clockd lands, ActivityWatch is the
# only measured source, and `timetrack ingest-aw` was a thing a human had to remember to
# run -- which means a day is captured only if someone thought about it that evening. The
# whole system exists because relying on memory is what failed.
#
# It refreshes TODAY on every run. AW keeps accumulating presence as the day goes on, so a
# single run at any one hour freezes the figure at that hour; refresh replaces today's own
# AW session each time. The replacement is guarded by provenance inside aw.py and can only
# ever touch a session AW itself wrote, never the hand-kept sheet.
#
# It ends with `doctor`, and a doctor failure fails the job. That is the point: the
# predecessor reported zero for months without erroring, so a capture job that only checks
# whether the command exited 0 would reproduce exactly that. Here a broken invariant turns
# the ops module amber on the bar.
job_main() {
  local tt out rc summary
  tt=$(command -v timetrack 2>/dev/null) || {
    ops_state timetrack-capture warn "timetrack is not on PATH (package not installed?)"
    return 0
  }

  # migrate and seed are idempotent and cheap; running them here means a fresh machine or a
  # new month needs no manual step before capture starts working.
  "$tt" migrate >/dev/null 2>&1 || {
    ops_state timetrack-capture fail "timetrack migrate failed"
    return 0
  }
  "$tt" seed >/dev/null 2>&1 || true

  out=$("$tt" ingest-aw 2>&1); rc=$?
  if [ "$rc" -ne 0 ]; then
    # aw-server being down is a real gap in the record, not a passing state
    ops_state timetrack-capture warn "ingest-aw failed: $(printf '%s' "$out" | tail -1)"
    return 0
  fi

  out=$("$tt" doctor 2>&1); rc=$?
  summary=$(printf '%s' "$out" | tail -1)
  if [ "$rc" -ne 0 ]; then
    ops_state timetrack-capture fail "$summary"
    return 0
  fi
  case $summary in
    *" 0 warned") ops_state timetrack-capture ok   "$summary" ;;
    *)            ops_state timetrack-capture warn "$summary" ;;
  esac
  return 0
}

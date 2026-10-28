#!/usr/bin/env bash
# Nothing leaves this machine (plan constraint 4). The timetrack package may talk to exactly
# two things: aw-server on loopback, and herdr over a unix socket. Checked twice:
#   static   every URL literal is loopback; no client library that could reach further;
#            every socket is AF_UNIX
#   runtime  an audit hook watches every socket.connect while the network-touching code
#            paths run, and fails on any address that is not loopback or a unix path
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
pkg="$root/nix/pkgs/timetrack/timetrack"
scripts=("$root"/home/private_dot_local/private_bin/executable_timetrack-*)
fail=0
bad() { echo "locality: $*"; fail=1; }

while IFS= read -r hit; do
  bad "non-loopback URL: $hit"
done < <(grep -rnoIE 'https?://[^"'"'"' )]+' "$pkg" "${scripts[@]}" \
           | grep -vE 'https?://(localhost|127\.0\.0\.1)(:[0-9]+)?(/|$)' || true)

while IFS= read -r hit; do
  bad "network client: $hit"
done < <(grep -rnIE '^\s*(import|from)\s+(requests|httpx|aiohttp|http\.client|smtplib|ftplib|telnetlib|xmlrpc|paramiko)\b' "$pkg" || true)

while IFS= read -r hit; do
  bad "socket that is not AF_UNIX: $hit"
done < <(grep -rnIE 'socket\.socket\(|create_connection\(' "$pkg" | grep -v 'AF_UNIX' || true)

while IFS= read -r hit; do
  bad "network tool in a script: $hit"
done < <(grep -nE '\b(curl|wget|nc|ncat|socat|ssh|scp|rsync)\b' "${scripts[@]}" | grep -vE '^\S+:[0-9]+:\s*#' || true)

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cd "$root/nix/pkgs/timetrack"
TIMETRACK_STATE_DIR="$tmp/state" XDG_CONFIG_HOME="$tmp/config" python3 - <<'PY' || fail=1
import ipaddress, socket, sys

seen, bad = [], []
def hook(event, args):
    if event != "socket.connect":
        return
    sock, addr = args
    seen.append(addr)
    if sock.family == socket.AF_UNIX:
        return
    host = addr[0]
    try:
        ok = ipaddress.ip_address(host).is_loopback
    except ValueError:
        ok = host == "localhost"
    if not ok:
        bad.append(addr)
sys.addaudithook(hook)

from timetrack import doctor, focuslog, presence, termclass
from timetrack.backfill import aw

presence.aw_presence()
# terminal attribution: the model call must be loopback, herdr must be a unix socket
for fn in (lambda: aw.bucket("afkstatus"), lambda: doctor._activitywatch([]),
           lambda: termclass.llm_label([{"k": "a", "item": {}}], [], set(), "m"),
           lambda: focuslog.snapshot(), lambda: doctor._focus([])):
    try: fn()
    except BaseException: pass   # no aw-server in CI: refused, but the connect was audited

if bad:
    print(f"locality FAILED: connected off-machine to {bad}")
    raise SystemExit(1)
if not seen:
    print("locality FAILED: audit hook saw no connect at all -- the runtime check is blind")
    raise SystemExit(1)
print(f"locality ok (static + {len(seen)} audited connect(s), all loopback or unix)")
PY
exit "$fail"

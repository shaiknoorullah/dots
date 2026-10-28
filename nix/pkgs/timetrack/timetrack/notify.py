"""Desktop notifications: a toast, and a question with buttons that may go unanswered.

notify-send talks to whatever notification server owns the bus (the Quickshell shell, or a
fallback). `ask` waits for a button, and on its own timeout closes the notification so no
stale button is left behind that nothing is listening to. No answer is returned as None,
never as a default: an unanswered question records an absence, not a choice.

Every subprocess goes through `run`/`popen` so tests replace them; nothing here may reach
a real desktop from a test.
"""
import select
import subprocess
import time

popen = subprocess.Popen


def toast(app, summary, body="", urgency="normal", ms=6000):
    try:
        popen(["notify-send", "-a", app, "-u", urgency, "-t", str(ms), summary, body],
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def close(nid):
    try:
        popen(["gdbus", "call", "--session", "--dest", "org.freedesktop.Notifications",
               "--object-path", "/org/freedesktop/Notifications",
               "--method", "org.freedesktop.Notifications.CloseNotification", str(nid)],
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def ask(app, summary, body, actions, timeout, urgency="normal"):
    """Show buttons; return the chosen key, or None on timeout, dismissal or failure.

    `actions` is [(key, label), ...]. `timeout` is seconds. The notification expires on
    the server at the same moment, and is closed explicitly in case the server ignores
    the expiry.
    """
    argv = ["notify-send", "-a", app, "-u", urgency, "-t", str(int(timeout * 1000)),
            "-p", "--wait"]
    argv += [f"--action={k}={label}" for k, label in actions]
    argv += [summary, body]
    try:
        proc = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.DEVNULL, text=True)
    except OSError:
        return None
    nid, answer = None, None
    deadline = time.monotonic() + timeout + 2
    keys = {k for k, _ in actions}
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([proc.stdout], [], [], left)
            if not ready:
                break
            line = proc.stdout.readline()
            if not line:
                break                    # closed without a button
            line = line.strip()
            if nid is None:
                nid = line               # -p prints the id first, always
                continue
            if line in keys:
                answer = line
                break
    finally:
        if answer is None and nid is not None:
            close(nid)
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    return answer

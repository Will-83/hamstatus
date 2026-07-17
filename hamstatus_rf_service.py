"""
HamStatus RF Activity Service

Tails the hotspot's own MMDVMHost log for RF transmissions from your callsign
and keeps status.json's "state" field in sync:

    key up                          -> "on_air" (immediately)
    key down, then 30s of silence   -> "monitoring"
    key down, then 2 min of silence -> "off_air"

Keying up again at any point cancels the pending downgrade and snaps
straight back to "on_air" -- so an ongoing back-and-forth QSO stays
"on_air" the whole time, not just during actual transmissions.

Requires a local config.py (NOT this file) containing your GitHub token --
see the setup instructions for what that file should contain. Never commit
config.py to your public repo.
"""

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import date

import config  # local file: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_PATH, GITHUB_BRANCH

WATCHED_CALLSIGN = "W8MB"
LOG_DIR = "/var/log/pi-star"

MONITORING_AFTER = 30    # seconds of silence before dropping to "monitoring"
OFF_AIR_AFTER = 120      # seconds of silence before dropping to "off_air"

START_RE = re.compile(r"received RF voice header from (\S+) to (.+)")
END_RE = re.compile(r"received RF end of voice transmission from (\S+) to (.+?), ([\d.]+) seconds")
LOST_RE = re.compile(r"RF voice transmission lost from (\S+) to (.+?), ([\d.]+) seconds")

state_lock = threading.Lock()
current_state = None
pending_timers = []


# ---------------------------------------------------------------- GitHub API

def api_url():
    return (f"https://api.github.com/repos/{config.GITHUB_OWNER}/"
            f"{config.GITHUB_REPO}/contents/{config.GITHUB_PATH}")


def github_get():
    req = urllib.request.Request(
        f"{api_url()}?ref={config.GITHUB_BRANCH}",
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def github_put(new_data, sha):
    body = json.dumps({
        "message": f"Auto-update status via RF watcher ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        "content": base64.b64encode(json.dumps(new_data, indent=2).encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": config.GITHUB_BRANCH,
    }).encode("utf-8")
    req = urllib.request.Request(
        api_url(), data=body, method="PUT",
        headers={
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ------------------------------------------------------------- State machine

def set_state(new_state, extra_fields=None):
    global current_state
    with state_lock:
        if new_state == current_state and not extra_fields:
            return
        try:
            current = github_get()
            data = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
            data["state"] = new_state
            if extra_fields:
                data.update(extra_fields)
            data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            github_put(data, current["sha"])
            current_state = new_state
            print(f"[status.json] state -> {new_state}" + (f" ({extra_fields})" if extra_fields else ""))
        except urllib.error.HTTPError as e:
            print(f"[error] GitHub API returned {e.code}: {e.reason}")
        except Exception as e:
            print(f"[error] failed to update status.json: {e}")


def cancel_pending_timers():
    global pending_timers
    for t in pending_timers:
        t.cancel()
    pending_timers = []


def schedule_transition(delay, new_state, extra_fields=None):
    t = threading.Timer(delay, lambda: set_state(new_state, extra_fields))
    t.daemon = True
    t.start()
    pending_timers.append(t)


def on_keyup(tg):
    cancel_pending_timers()
    set_state("on_air", extra_fields={"mode": "DMR", "activity": tg})
    print(f"\U0001F534 ON AIR -> {tg}")


def on_keydown(tg, duration):
    print(f"\u26AA key released ({duration}s) -- monitoring in {MONITORING_AFTER}s, off_air in {OFF_AIR_AFTER}s unless you key up again")
    cancel_pending_timers()
    schedule_transition(MONITORING_AFTER, "monitoring", extra_fields={"activity": "Listening"})
    schedule_transition(OFF_AIR_AFTER, "off_air")


# ------------------------------------------------------------------ Log tail

def current_log_path():
    return os.path.join(LOG_DIR, f"MMDVM-{date.today().isoformat()}.log")


def handle_line(line, callsign):
    m = START_RE.search(line)
    if m and m.group(1) == callsign:
        on_keyup(m.group(2).strip())
        return

    m = END_RE.search(line)
    if m and m.group(1) == callsign:
        on_keydown(m.group(2).strip(), m.group(3))
        return

    m = LOST_RE.search(line)
    if m and m.group(1) == callsign:
        on_keydown(m.group(2).strip(), m.group(3))
        return


def watch_file(path, callsign):
    f = open(path, "r")
    f.seek(0, os.SEEK_END)
    try:
        while True:
            line = f.readline()
            if line:
                handle_line(line, callsign)
            else:
                if current_log_path() != path:
                    return
                time.sleep(0.5)
    finally:
        f.close()


def main():
    print(f"HamStatus RF service running for {WATCHED_CALLSIGN}. Ctrl+C to stop.")
    while True:
        path = current_log_path()
        if not os.path.exists(path):
            time.sleep(1)
            continue
        watch_file(path, WATCHED_CALLSIGN)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")

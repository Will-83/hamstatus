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

# Your primary DMR master -- this is static since the local log doesn't tell us
# which external network a given talkgroup actually belongs to. Change if
# BrandMeister isn't your main master.
NETWORK_NAME = "BrandMeister"

# WPSD downloads this itself (refreshed by its nightly update process) --
# it's BrandMeister's full talkgroup/reflector list, semicolon-delimited:
# Dest ID;Option;Name;Description  (Option: TG=0, REF=1, PC=2)
TG_LIST_PATH = "/usr/local/etc/TGList_BM.txt"

# Manual overrides/additions -- checked BEFORE the downloaded list, so use
# this for anything not covered there (e.g. TGIF-specific talkgroups, since
# TGList_BM.txt is BrandMeister-only) or to override a name you don't like.
TG_NAMES = {}

_tg_list_cache = {}
_tg_list_mtime = None


def load_tg_list():
    """(Re)loads TG_LIST_PATH if it exists and has changed since last load.
    Safe to call often -- it's a cheap no-op when the file is unchanged, and
    fails quietly (keeping whatever was already cached) if it's missing."""
    global _tg_list_cache, _tg_list_mtime
    try:
        mtime = os.path.getmtime(TG_LIST_PATH)
    except OSError:
        return
    if mtime == _tg_list_mtime:
        return

    names = {}
    try:
        with open(TG_LIST_PATH, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(";")
                if len(parts) < 3:
                    continue
                try:
                    tg_id = int(parts[0])
                except ValueError:
                    continue
                name = parts[2].strip()
                if name:
                    names[tg_id] = name
    except OSError:
        return

    _tg_list_cache = names
    _tg_list_mtime = mtime
    print(f"[tg list] loaded {len(names)} talkgroup names from {TG_LIST_PATH}")


def lookup_tg_name(tg_id):
    if tg_id in TG_NAMES:
        return TG_NAMES[tg_id]
    load_tg_list()
    return _tg_list_cache.get(tg_id, str(tg_id))

START_RE = re.compile(r"received RF voice header from (\S+) to (.+)")
END_RE = re.compile(r"received RF end of voice transmission from (\S+) to (.+?), ([\d.]+) seconds")
LOST_RE = re.compile(r"RF voice transmission lost from (\S+) to (.+?), ([\d.]+) seconds")
TG_RE = re.compile(r"^TG (\d+)$")

state_lock = threading.Lock()
current_state = None
pending_timers = []


def parse_talkgroup(destination_text):
    """Returns (tg_id, tg_name) for a group-call destination like 'TG 31291',
    or (None, None) for anything else (e.g. a private call to a radio ID)."""
    m = TG_RE.match(destination_text.strip())
    if not m:
        return None, None
    tg_id = int(m.group(1))
    return tg_id, lookup_tg_name(tg_id)


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

def set_state(new_state, extra_fields=None, remove_fields=None):
    global current_state
    with state_lock:
        if new_state == current_state and not extra_fields and not remove_fields:
            return
        try:
            current = github_get()
            data = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
            data["state"] = new_state
            if extra_fields:
                data.update(extra_fields)
            if remove_fields:
                for key in remove_fields:
                    data.pop(key, None)
            data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            github_put(data, current["sha"])
            current_state = new_state
            detail = f" +{extra_fields}" if extra_fields else ""
            detail += f" -{remove_fields}" if remove_fields else ""
            print(f"[status.json] state -> {new_state}{detail}")
        except urllib.error.HTTPError as e:
            print(f"[error] GitHub API returned {e.code}: {e.reason}")
        except Exception as e:
            print(f"[error] failed to update status.json: {e}")


def cancel_pending_timers():
    global pending_timers
    for t in pending_timers:
        t.cancel()
    pending_timers = []


def schedule_transition(delay, new_state, extra_fields=None, remove_fields=None):
    t = threading.Timer(delay, lambda: set_state(new_state, extra_fields, remove_fields))
    t.daemon = True
    t.start()
    pending_timers.append(t)


def on_keyup(tg_text):
    cancel_pending_timers()
    tg_id, tg_name = parse_talkgroup(tg_text)
    extra = {"mode": "DMR", "activity": tg_name or tg_text}
    if tg_id is not None:
        extra.update({"talkgroup": tg_id, "talkgroup_name": tg_name, "network": NETWORK_NAME})
    set_state("on_air", extra_fields=extra)
    print(f"\U0001F534 ON AIR -> {tg_text}")


def on_keydown(tg, duration):
    print(f"\u26AA key released ({duration}s) -- monitoring in {MONITORING_AFTER}s, off_air in {OFF_AIR_AFTER}s unless you key up again")
    cancel_pending_timers()
    # Talkgroup fields stay in place through "monitoring" -- the dynamic TG is
    # still linked during that window, it's only fully dropped at "off_air".
    schedule_transition(MONITORING_AFTER, "monitoring", extra_fields={"activity": "Listening"})
    schedule_transition(OFF_AIR_AFTER, "off_air", remove_fields=["talkgroup", "talkgroup_name", "network"])


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

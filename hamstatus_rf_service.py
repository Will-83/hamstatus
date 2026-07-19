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

# WPSD downloads these itself (refreshed by its nightly update process).
# TGList_BM.txt and TGList_TGIF.txt are "ID;Option;Name;Description" and
# "ID;Name" respectively. DMR2YSF names actually live in YSFHosts.txt (the
# traditional YSF reflector host list) instead of TGList_YSF.txt, which
# turned out to just echo the ID back as its own "name" with no real data --
# confirmed against the user's real file contents. YSFHosts.txt has its own
# distinct layout: ID;Name;Description;Host;Port;... (name in field 1, not
# field 2 like the BrandMeister list).
TG_LIST_BM_PATH = "/usr/local/etc/TGList_BM.txt"
TG_LIST_TGIF_PATH = "/usr/local/etc/TGList_TGIF.txt"
TG_LIST_YSF_PATH = "/usr/local/etc/YSFHosts.txt"

# Default comment shown per state -- editable to taste. {name} is replaced
# with the talkgroup's friendly name when there is one.
MSG_ON_AIR_TG = "Transmitting on {name}"
MSG_ON_AIR_PRIVATE = "Transmitting"
MSG_MONITORING = "Just wrapped up -- still listening."
MSG_OFF_AIR = "Monitoring the local repeater. Drop a call!"

# Manual overrides/additions -- checked BEFORE any downloaded list, so use
# this to override a name you don't like, or add one that's missing from
# the relevant TGList file entirely.
TG_NAMES = {}

# One mtime/name cache per list file, keyed by path -- lets load_tg_list()
# stay a single small function instead of three near-identical copies.
_tg_list_caches = {}   # path -> {tg_id: name}
_tg_list_mtimes = {}   # path -> mtime float


def load_tg_list(path, name_index=None):
    """(Re)loads the given list file if it exists and has changed since last
    load. Safe to call often -- cheap no-op when unchanged, fails quietly
    (keeping whatever was already cached) if the file is missing.

    Three layouts seen in the wild: BrandMeister's TGList_BM.txt is
    "ID;Option;Name;Description" (name in field 2); WPSD's own TGIF
    cross-reference is just "ID;Name" (name in field 1); YSFHosts.txt is
    "ID;Name;Description;Host;Port;..." (name in field 1, but with more
    fields than the TGIF case, so it can't share that same auto-detection --
    pass name_index explicitly for any file that doesn't fit the first two
    patterns)."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    if _tg_list_mtimes.get(path) == mtime:
        return

    names = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(";")
                if len(parts) < 2:
                    continue
                try:
                    key = int(parts[0])
                except ValueError:
                    continue
                if name_index is not None:
                    idx = name_index
                else:
                    idx = 2 if len(parts) >= 3 else 1
                if idx >= len(parts):
                    continue
                name = parts[idx].strip()
                if name:
                    names[key] = name
    except OSError:
        return

    _tg_list_caches[path] = names
    _tg_list_mtimes[path] = mtime
    print(f"[tg list] loaded {len(names)} names from {path}")


def lookup_tg_name(tg_id, list_path, lookup_key=None, name_index=None):
    """tg_id is what actually gets stored/displayed as the talkgroup. Some
    list files (WPSD's TGIF cross-reference) are indexed by a different key
    than that -- the full DMRGateway-encoded number, not the bare TG -- so
    lookup_key lets the caller supply that separately. Falls back to
    str(tg_id), never str(lookup_key), so a miss still shows the short
    number rather than the confusing 7-digit encoded form."""
    if lookup_key is None:
        lookup_key = tg_id
    if tg_id in TG_NAMES:
        return TG_NAMES[tg_id]
    load_tg_list(list_path, name_index=name_index)
    return _tg_list_caches.get(list_path, {}).get(lookup_key, str(tg_id))


START_RE = re.compile(r"received (?:RF|network) voice header from (\S+) to (.+)")
END_RE = re.compile(r"received (?:RF|network) end of voice transmission from (\S+) to (.+?), ([\d.]+) seconds")
LOST_RE = re.compile(r"(?:RF|network) voice transmission lost from (\S+) to (.+?), ([\d.]+) seconds")
TG_RE = re.compile(r"^TG (\d+)$")

state_lock = threading.Lock()
current_state = None
pending_timers = []


def parse_talkgroup(destination_text):
    """Returns (tg_id, tg_name, network) for a group-call destination like
    'TG 31291', or (None, None, None) for anything else (e.g. a private call
    to a radio ID).

    WPSD/DMRGateway encode which network a call is bridged to directly in
    the TG number: a leading 5 means TGIF, a leading 7 means DMR2YSF, both
    followed by the real TG/reflector number (zero-padded to fill out a
    7-digit total). Anything else is a plain BrandMeister talkgroup. This
    means the network can be determined from the MMDVM log alone, with no
    need to also read DMRGateway.log.

    Known gap: FCS reflectors reached via DMR2YSF use a further "100"-prefixed
    sub-code within the 6-digit field (e.g. 7100290 = FCS2-90) that isn't
    decoded here yet -- untested, so it's left as a raw number for now rather
    than guessed at.
    """
    m = TG_RE.match(destination_text.strip())
    if not m:
        return None, None, None

    raw = m.group(1)
    if len(raw) == 7 and raw[0] == "5":
        tg_id = int(raw[1:])
        return tg_id, lookup_tg_name(tg_id, TG_LIST_TGIF_PATH, lookup_key=int(raw)), "TGIF"
    if len(raw) == 7 and raw[0] == "7":
        tg_id = int(raw[1:])
        return tg_id, lookup_tg_name(tg_id, TG_LIST_YSF_PATH, name_index=1), "DMR2YSF"

    tg_id = int(raw)
    return tg_id, lookup_tg_name(tg_id, TG_LIST_BM_PATH), "BrandMeister"


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

MAX_CONFLICT_RETRIES = 3


def set_state(new_state, extra_fields=None, remove_fields=None):
    global current_state
    with state_lock:
        if new_state == current_state and not extra_fields and not remove_fields:
            return

        for attempt in range(1, MAX_CONFLICT_RETRIES + 1):
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
                if extra_fields and "last_heard" in extra_fields:
                    # Stamped here, not when the transition was scheduled --
                    # this needs to reflect the moment it actually went
                    # off_air (2 minutes later), not when the key was released.
                    data["last_heard"]["time"] = data["last_updated"]
                github_put(data, current["sha"])
                current_state = new_state
                detail = f" +{extra_fields}" if extra_fields else ""
                detail += f" -{remove_fields}" if remove_fields else ""
                print(f"[status.json] state -> {new_state}{detail}")
                return
            except urllib.error.HTTPError as e:
                if e.code == 409 and attempt < MAX_CONFLICT_RETRIES:
                    # Another writer (the APRS service, most likely) updated
                    # status.json between our GET and PUT. Re-fetching gets
                    # the current sha, so simply retrying the whole cycle
                    # resolves this rather than dropping the update.
                    print(f"[retry] status.json changed elsewhere (409), re-fetching and retrying ({attempt}/{MAX_CONFLICT_RETRIES})")
                    continue
                print(f"[error] GitHub API returned {e.code}: {e.reason}")
                return
            except Exception as e:
                print(f"[error] failed to update status.json: {e}")
                return


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
    tg_id, tg_name, network = parse_talkgroup(tg_text)
    extra = {"mode": "DMR", "activity": tg_name or tg_text}
    if tg_id is not None:
        extra.update({"talkgroup": tg_id, "talkgroup_name": tg_name, "network": network})
        extra["custom_message"] = MSG_ON_AIR_TG.format(name=tg_name)
    else:
        extra["custom_message"] = MSG_ON_AIR_PRIVATE
    set_state("on_air", extra_fields=extra)
    print(f"\U0001F534 ON AIR -> {tg_text}")


def on_keydown(tg, duration):
    print(f"\u26AA key released ({duration}s) -- monitoring in {MONITORING_AFTER}s, off_air in {OFF_AIR_AFTER}s unless you key up again")
    cancel_pending_timers()

    # Snapshot what was active, so it isn't just lost once the talkgroup
    # fields get cleared at off_air -- "time" gets filled in by set_state
    # itself when this transition actually fires, 2 minutes from now.
    tg_id, tg_name, network = parse_talkgroup(tg)
    last_heard = {"mode": "DMR"}
    if tg_id is not None:
        last_heard.update({"talkgroup": tg_id, "talkgroup_name": tg_name, "network": network})

    # Talkgroup fields stay in place through "monitoring" -- the dynamic TG is
    # still linked during that window, it's only fully dropped at "off_air".
    schedule_transition(MONITORING_AFTER, "monitoring",
                         extra_fields={"activity": "Listening", "custom_message": MSG_MONITORING})
    schedule_transition(OFF_AIR_AFTER, "off_air",
                         extra_fields={"custom_message": MSG_OFF_AIR, "last_heard": last_heard},
                         remove_fields=["talkgroup", "talkgroup_name", "network"])


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

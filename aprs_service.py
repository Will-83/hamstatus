"""
HamStatus APRS Service

Maintains a persistent, read-only connection to APRS-IS (passcode -1, no
transmit capability), filtered to one callsign via the server's own budlist
filter. Updates status.json's "aprs" section whenever a new beacon or
outgoing message from that callsign arrives.

Coordinates are ALWAYS rounded to ~1km precision before being written,
regardless of whether the source beacon was full-precision or already
ambiguous -- so the true precise location never touches status.json at all,
not even transiently. See aprs_test_listener.py for the parsing logic this
is built on, validated against real beacons from two different device types.

Requires the same local config.py as hamstatus_rf_service.py (GITHUB_TOKEN,
GITHUB_OWNER, GITHUB_REPO, GITHUB_PATH, GITHUB_BRANCH) -- never commit that
file or upload it anywhere public.

Run:
    python3 aprs_service.py
Stop:
    Ctrl+C
"""

import base64
import json
import re
import socket
import time
import urllib.error
import urllib.request

import config  # local file: GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_PATH, GITHUB_BRANCH

CALLSIGN = "W8MB"
APRS_IS_HOST = "rotate.aprs2.net"
APRS_IS_PORT = 14580

# Coordinates are rounded to this many decimal places before ever being
# written to status.json -- roughly 1km / 0.6mi resolution. This applies
# unconditionally, even to beacons that already arrived full-precision.
COORD_ROUND_DP = 2

RECONNECT_DELAY = 15  # seconds to wait before retrying a dropped connection

POS_RE = re.compile(
    r"^(\d{2})([\d ]{2}\.[\d ]{2})([NS])(.)(\d{3})([\d ]{2}\.[\d ]{2})([EW])(.)(.*)$"
)
MSG_RE = re.compile(r"^:([A-Z0-9\- ]{9}):(.*)$")
COURSE_SPEED_RE = re.compile(r"^(\d{3})/(\d{3})")
ALTITUDE_RE = re.compile(r"/A=(\d{6})")


# ---------------------------------------------------------------- Parsing
# (Same logic as aprs_test_listener.py, validated against real beacons from
# both a phone tracker and a radio -- see that file for the test coverage.)

def decode_position(payload):
    m = POS_RE.match(payload)
    if not m:
        return None
    lat_deg, lat_min, lat_hem, sym_table, lon_deg, lon_min, lon_hem, sym_code, comment = m.groups()

    lat_min_val = float(lat_min.replace(" ", "0"))
    lon_min_val = float(lon_min.replace(" ", "0"))
    lat = int(lat_deg) + lat_min_val / 60
    if lat_hem == "S":
        lat = -lat
    lon = int(lon_deg) + lon_min_val / 60
    if lon_hem == "W":
        lon = -lon

    course = speed = altitude_ft = None
    cs_match = COURSE_SPEED_RE.match(comment)
    if cs_match:
        course, speed = int(cs_match.group(1)), int(cs_match.group(2))
        comment = comment[cs_match.end():]
    alt_match = ALTITUDE_RE.search(comment)
    if alt_match:
        altitude_ft = int(alt_match.group(1))
        comment = comment[:alt_match.start()] + comment[alt_match.end():]

    return {
        # Rounded unconditionally -- see COORD_ROUND_DP comment above.
        "lat": round(lat, COORD_ROUND_DP),
        "lon": round(lon, COORD_ROUND_DP),
        "symbol": sym_table + sym_code,
        "course": course,
        "speed_mph": speed,
        "altitude_ft": altitude_ft,
        "comment": comment.strip(),
    }


def parse_packet(line):
    if ">" not in line or ":" not in line:
        return None
    header, payload = line.split(":", 1)
    if ">" not in header:
        return None
    source, rest = header.split(">", 1)
    dest_and_path = rest.split(",")
    dest = dest_and_path[0] if dest_and_path else ""
    return source, dest, payload


# ------------------------------------------------------------------ GitHub

def api_url():
    return (f"https://api.github.com/repos/{config.GITHUB_OWNER}/"
            f"{config.GITHUB_REPO}/contents/{config.GITHUB_PATH}")


def github_get():
    req = urllib.request.Request(
        f"{api_url()}?ref={config.GITHUB_BRANCH}",
        headers={"Authorization": f"Bearer {config.GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def github_put(new_data, sha):
    body = json.dumps({
        "message": f"Auto-update APRS status ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        "content": base64.b64encode(json.dumps(new_data, indent=2).encode("utf-8")).decode("ascii"),
        "sha": sha,
        "branch": config.GITHUB_BRANCH,
    }).encode("utf-8")
    req = urllib.request.Request(
        api_url(), data=body, method="PUT",
        headers={"Authorization": f"Bearer {config.GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


MAX_CONFLICT_RETRIES = 3


def update_aprs_section(patch):
    """Merges `patch` into status.json's top-level "aprs" object, leaving
    every other field (state, mode, talkgroup, etc.) completely untouched."""
    for attempt in range(1, MAX_CONFLICT_RETRIES + 1):
        try:
            current = github_get()
            data = json.loads(base64.b64decode(current["content"]).decode("utf-8"))
            aprs = data.get("aprs", {})
            aprs.update(patch)
            data["aprs"] = aprs
            github_put(data, current["sha"])
            print(f"[status.json] aprs -> {patch}")
            return
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < MAX_CONFLICT_RETRIES:
                # Another writer (the RF service, most likely) updated
                # status.json between our GET and PUT -- re-fetch and retry
                # with the current sha rather than dropping this update.
                print(f"[retry] status.json changed elsewhere (409), re-fetching and retrying ({attempt}/{MAX_CONFLICT_RETRIES})")
                continue
            print(f"[error] GitHub API returned {e.code}: {e.reason}")
            return
        except Exception as e:
            print(f"[error] failed to update status.json: {e}")
            return


# -------------------------------------------------------------- Packet handling

def handle_line(line, callsign):
    parsed = parse_packet(line)
    if not parsed:
        return
    source, dest, payload = parsed

    if source.split("-")[0] != callsign:
        return
    if not payload:
        return
    type_char = payload[0]

    if type_char in "!=/@":
        body = payload[1:]
        if type_char in "/@":
            body = body[7:]
        pos = decode_position(body)
        if not pos:
            print(f"\U0001F4CD BEACON from {source} (position format not decoded, skipped): {payload}")
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"\U0001F4CD BEACON from {source} -> lat={pos['lat']}, lon={pos['lon']}, "
              f"symbol={pos['symbol']}, comment=\"{pos['comment']}\"")
        update_aprs_section({
            "last_beacon": now,
            "position": {"lat": pos["lat"], "lon": pos["lon"]},
            "symbol": pos["symbol"],
            "course": pos["course"],
            "speed_mph": pos["speed_mph"],
            "altitude_ft": pos["altitude_ft"],
            "comment": pos["comment"],
        })

    elif type_char == ":":
        m = MSG_RE.match(payload)
        if not m:
            return
        addressee, text = m.groups()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"\u2709\uFE0F  MESSAGE from {source} to {addressee.strip()}: {text}")
        update_aprs_section({
            "last_message": {"to": addressee.strip(), "text": text, "time": now}
        })


# ------------------------------------------------------------------ Connection

def run_once():
    print(f"Connecting to {APRS_IS_HOST}:{APRS_IS_PORT} as read-only, filtering to {CALLSIGN}...")
    sock = socket.create_connection((APRS_IS_HOST, APRS_IS_PORT), timeout=30)
    sock_file = sock.makefile("r", encoding="utf-8", errors="replace", newline="\n")
    try:
        login = f"user {CALLSIGN} pass -1 vers HamStatus-APRS 1.0 filter b/{CALLSIGN}*\r\n"
        sock.sendall(login.encode("utf-8"))
        print(f"Sent login: {login.strip()}")

        for line in sock_file:
            line = line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            handle_line(line, CALLSIGN)
    finally:
        sock.close()


def main():
    print(f"HamStatus APRS service starting for {CALLSIGN}. Ctrl+C to stop.")
    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        except Exception as e:
            print(f"[connection error] {e} -- reconnecting in {RECONNECT_DELAY}s")
        time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()

"""
WPSD Local RF Activity Watcher — Test

Watches your hotspot's own MMDVMHost log file (the same source your WPSD
dashboard's Last Heard panel already reads from) for RF transmissions from
your callsign. This makes zero network calls to Brandmeister — it's pure
local log tailing, so it isn't affected by anything on the Brandmeister side.

Run:
    python3 mmdvm_watcher.py
Stop:
    Ctrl+C
"""

import os
import re
import time
from datetime import date

WATCHED_CALLSIGN = "W8MB"
LOG_DIR = "/var/log/pi-star"

START_RE = re.compile(r"received RF voice header from (\S+) to (.+)")
END_RE = re.compile(r"received RF end of voice transmission from (\S+) to (.+?), ([\d.]+) seconds")
LOST_RE = re.compile(r"RF voice transmission lost from (\S+) to (.+?), ([\d.]+) seconds")


def current_log_path():
    return os.path.join(LOG_DIR, f"MMDVM-{date.today().isoformat()}.log")


def handle_line(line, callsign):
    m = START_RE.search(line)
    if m and m.group(1) == callsign:
        print(f"\U0001F534 ON AIR -> {m.group(2).strip()}")
        return

    m = END_RE.search(line)
    if m and m.group(1) == callsign:
        print(f"\u26AA OFF AIR — {m.group(3)}s to {m.group(2).strip()}")
        return

    m = LOST_RE.search(line)
    if m and m.group(1) == callsign:
        print(f"\u26AA OFF AIR (lost) — {m.group(3)}s to {m.group(2).strip()}")
        return


def watch_file(path, callsign):
    """Tails one day's log file. Returns (instead of raising) when the date
    rolls over to a new file, so main() can reopen the new one."""
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
    print(f"Watching for RF activity from {WATCHED_CALLSIGN} in {LOG_DIR}... (Ctrl+C to stop)")
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

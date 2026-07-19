"""
Brandmeister Last Heard — Test Listener (Python version)

Watches the live Brandmeister network feed for one DMR radio ID and prints
a line whenever that station keys up or unkeys. Nothing is saved anywhere —
this is purely to prove the detection works before we automate anything.

SETUP (one-time):
    1. Install Python from https://python.org if you don't already have it.
       On Windows, check "Add python.exe to PATH" during install.
    2. Open a terminal (Terminal.app on Mac, Command Prompt or PowerShell on
       Windows) and run:
           pip install "python-socketio[client]"
       (On Mac you may need "pip3" instead of "pip".)

RUN:
    Save this file as bm_test_listener.py, then in the same terminal run:
        python bm_test_listener.py
    (or "python3 bm_test_listener.py" on Mac)

    Key up on your radio or hotspot and watch for output. Press Ctrl+C to stop.
"""

import json
import socketio

# Your Brandmeister DMR radio ID (7-digit personal ID from radioid.net)
WATCHED_ID = 3193254

sio = socketio.Client()


@sio.event
def connect():
    # Brandmeister requires an explicit subscribe after connecting -- without
    # this the socket connects fine but silently never receives any events.
    sio.emit("join", "everything")
    print(f"Connected. Listening for DMR ID {WATCHED_ID}... (Ctrl+C to stop)")


@sio.event
def connect_error(data):
    print("Connection failed:", data)


@sio.event
def disconnect():
    print("Disconnected.")


def id_matches(source_id, base_id):
    """Matches the bare personal ID against a SourceID that may have a
    2-digit hotspot ESSID suffix appended (e.g. base 3193254 vs a hotspot
    reporting 319325401)."""
    if source_id == base_id:
        return True
    s, b = str(source_id), str(base_id)
    return len(s) == len(b) + 2 and s.startswith(b)


event_count = 0


@sio.on("mqtt")
def on_mqtt(data):
    global event_count
    try:
        call = json.loads(data["payload"])
    except (KeyError, TypeError, json.JSONDecodeError):
        return

    event_count += 1
    if event_count % 25 == 0:
        print(f"... still connected, {event_count} network events seen so far (not necessarily yours)")

    if not id_matches(call.get("SourceID"), WATCHED_ID):
        return

    event = call.get("Event")
    tg = call.get("DestinationName") or call.get("DestinationID")

    if event == "Session-Start":
        print(f"\U0001F534 ON AIR — TG {tg} (slot {call.get('Slot')}) — source ID {call.get('SourceID')}")
    elif event == "Session-Stop":
        start, stop = call.get("Start", 0), call.get("Stop", 0)
        duration = (stop - start) if stop else "?"
        print(f"\u26AA Session ended — {duration}s on TG {tg}")


if __name__ == "__main__":
    sio.connect(
        url="https://api.brandmeister.network",
        socketio_path="/lh/socket.io",
        transports=["websocket"],
    )
    sio.wait()

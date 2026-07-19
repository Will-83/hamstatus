"""
Brandmeister Last Heard — Talkgroup Test Listener (Python version)

Watches the live Brandmeister network feed for activity on one talkgroup,
regardless of who's transmitting. Nothing is saved anywhere -- this is a
diagnostic tool to confirm the live feed is reachable and to see what
source ID a real key-up actually reports.

SETUP (one-time):
    1. Install Python from https://python.org if you don't already have it.
       On Windows, check "Add python.exe to PATH" during install.
    2. Open a terminal (Terminal.app on Mac, Command Prompt or PowerShell on
       Windows) and run:
           pip install "python-socketio[client]"
       (On Mac you may need "pip3" instead of "pip".)

RUN:
    Save this file as bm_tg_test_listener.py, then in the same terminal run:
        python bm_tg_test_listener.py
    (or "python3 bm_tg_test_listener.py" on Mac)

    Key up on the watched talkgroup and watch for output. Press Ctrl+C to stop.
"""

import json
import socketio

WATCHED_TG = 31291

sio = socketio.Client()


@sio.event
def connect():
    # Brandmeister requires an explicit subscribe after connecting -- without
    # this the socket connects fine but silently never receives any events.
    sio.emit("join", f"dst_{WATCHED_TG}")
    print(f"Connected and joined dst_{WATCHED_TG}. Watching for activity... (Ctrl+C to stop)")


@sio.event
def connect_error(data):
    print("Connection failed:", data)


@sio.event
def disconnect():
    print("Disconnected.")


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
        print(f"... still connected, {event_count} network events seen so far")

    dest = call.get("DestinationID")
    dest_name = call.get("DestinationName")
    if dest != WATCHED_TG and str(dest_name) != str(WATCHED_TG):
        return

    event = call.get("Event")
    src = call.get("SourceID")
    if event == "Session-Start":
        print(f"\U0001F534 TG {WATCHED_TG} ON AIR — source ID {src} (slot {call.get('Slot')})")
    elif event == "Session-Stop":
        start, stop = call.get("Start", 0), call.get("Stop", 0)
        duration = (stop - start) if stop else "?"
        print(f"⚪ TG {WATCHED_TG} session ended — {duration}s — source ID {src}")


if __name__ == "__main__":
    sio.connect(
        url="https://api.brandmeister.network",
        socketio_path="/lh/socket.io",
        transports=["websocket"],
    )
    sio.wait()

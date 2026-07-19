"""
TGIF Network Last Heard — Test Listener (Python version)

Watches TGIF Network's live "Last Heard" feed. TGIF is a separate DMR
network from Brandmeister with its own (undocumented, reverse-engineered
from tgif.network/lastheard.php) socket.io protocol. Nothing is saved
anywhere -- this is purely a diagnostic tool to confirm the feed is
readable.

SETUP (one-time):
    1. Install Python from https://python.org if you don't already have it.
       On Windows, check "Add python.exe to PATH" during install.
    2. Open a terminal (Terminal.app on Mac, Command Prompt or PowerShell on
       Windows) and run:
           pip install "python-socketio[client]"
       (On Mac you may need "pip3" instead of "pip".)

RUN:
    Save this file as tgif_test_listener.py, then in the same terminal run:
        python tgif_test_listener.py
    (or "python3 tgif_test_listener.py" on Mac)

    TGIF is a busy network -- you should see live traffic within seconds
    without needing to key up yourself. Press Ctrl+C to stop.
"""

import json
import socketio

sio = socketio.Client()


@sio.event
def connect():
    print("Connected. Sending anonymous handshake...")
    # TGIF's protocol: handshake with an empty token for anonymous/public
    # access, then wait for a "status" 200 before asking for data.
    sio.emit("handshake", "")


@sio.event
def connect_error(data):
    print("Connection failed:", data)


@sio.event
def disconnect():
    print("Disconnected.")


@sio.on("status")
def on_status(data):
    print(f"Handshake status: {data}")
    if data == 200:
        msg = {"scope": "*", "page": "lastheard", "action": "lh-backlog"}
        sio.emit("cli-state", msg)


@sio.on("lastheard_backlog")
def on_backlog(records):
    print(f"Backlog received: {len(records)} records")
    for r in records[:3]:
        print(" ", json.dumps(r))


@sio.on("lastheard")
def on_lastheard(data):
    print("LIVE:", json.dumps(data))


if __name__ == "__main__":
    sio.connect(
        url="https://tgif.network",
        transports=["websocket"],
    )
    sio.wait()

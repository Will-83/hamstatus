"""
APRS-IS Test Listener

Connects to APRS-IS read-only (passcode -1, no transmit capability, no real
passcode needed) and filters to just one callsign's traffic using the
server's own budlist filter -- so this isn't pulling the global firehose,
just your station's packets.

Classifies each packet as a position/beacon report or a message, and does
basic parsing of the common (uncompressed) position format. Compressed-format
positions and MIC-E encoded packets are printed raw but not decoded yet --
we'll add that once we see what your actual beacons look like.

Run:
    python3 aprs_test_listener.py
Stop:
    Ctrl+C
"""

import re
import socket
import time

CALLSIGN = "W8MB"
APRS_IS_HOST = "rotate.aprs2.net"
APRS_IS_PORT = 14580

# Uncompressed position report: DDMM.MMn/DDDMM.MMe SYMBOL comment
POS_RE = re.compile(
    r"^(\d{2})(\d{2}\.\d{2})([NS])(.)(\d{3})(\d{2}\.\d{2})([EW])(.)(.*)$"
)
MSG_RE = re.compile(r"^:([A-Z0-9\- ]{9}):(.*)$")


def decode_position(payload):
    m = POS_RE.match(payload)
    if not m:
        return None
    lat_deg, lat_min, lat_hem, sym_table, lon_deg, lon_min, lon_hem, sym_code, comment = m.groups()
    lat = int(lat_deg) + float(lat_min) / 60
    if lat_hem == "S":
        lat = -lat
    lon = int(lon_deg) + float(lon_min) / 60
    if lon_hem == "W":
        lon = -lon
    # Strip a trailing message-id-looking suffix isn't needed here -- comment is free text
    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "symbol_table": sym_table,
        "symbol_code": sym_code,
        "comment": comment.strip(),
    }


def parse_packet(line):
    """Splits a raw TNC2-format line into (source, dest, path, payload)."""
    if ">" not in line or ":" not in line:
        return None
    header, payload = line.split(":", 1)
    if ">" not in header:
        return None
    source, rest = header.split(">", 1)
    dest_and_path = rest.split(",")
    dest = dest_and_path[0] if dest_and_path else ""
    return source, dest, payload


def handle_line(line, callsign):
    parsed = parse_packet(line)
    if not parsed:
        return
    source, dest, payload = parsed

    # Only care about packets FROM our callsign (base call, ignore SSID like -9)
    if source.split("-")[0] != callsign:
        return

    if not payload:
        return
    type_char = payload[0]

    if type_char in "!=/@":
        # Position reports with a timestamp (/ or @) have 7 extra chars
        # (timestamp + format indicator) before the actual position data.
        body = payload[1:]
        if type_char in "/@":
            body = body[7:]
        pos = decode_position(body)
        if pos:
            print(f"\U0001F4CD BEACON from {source} -> lat={pos['lat']}, lon={pos['lon']}, "
                  f"symbol={pos['symbol_table']}{pos['symbol_code']}, comment=\"{pos['comment']}\"")
        else:
            print(f"\U0001F4CD BEACON from {source} (position format not decoded): {payload}")

    elif type_char == ":":
        m = MSG_RE.match(payload)
        if m:
            addressee, text = m.groups()
            print(f"\u2709\uFE0F  MESSAGE from {source} to {addressee.strip()}: {text}")
        else:
            print(f"\u2709\uFE0F  MESSAGE-type packet from {source} (unparsed): {payload}")

    elif type_char == ">":
        print(f"\U0001F4E2 STATUS from {source}: {payload[1:]}")

    else:
        print(f"\u2753 Other packet type ({type_char!r}) from {source}: {payload}")


def main():
    print(f"Connecting to {APRS_IS_HOST}:{APRS_IS_PORT} as read-only, filtering to {CALLSIGN}...")
    sock = socket.create_connection((APRS_IS_HOST, APRS_IS_PORT), timeout=30)
    sock_file = sock.makefile("r", encoding="utf-8", errors="replace", newline="\n")

    login = f"user {CALLSIGN} pass -1 vers HamStatus-Test 1.0 filter b/{CALLSIGN}*\r\n"
    sock.sendall(login.encode("utf-8"))
    print(f"Sent login: {login.strip()}")
    print("Waiting for traffic... (Ctrl+C to stop; this may sit quiet until you next beacon)\n")

    try:
        for line in sock_file:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#"):
                print(f"[server] {line}")
                continue
            handle_line(line, CALLSIGN)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()


if __name__ == "__main__":
    main()

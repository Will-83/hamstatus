"""
HamStatus PNG Renderer

Renders status.json as a static image in the same "vintage rig display"
style as widget.html, for platforms that won't run JavaScript inside an
iframe (QRZ bio pages, forum signatures, etc).

Run manually:
    python3 render_status_png.py [path/to/status.json] [path/to/output.png]
Defaults to ./status.json -> ./status.png if no arguments given.

Normally this runs automatically via a GitHub Action every time status.json
changes -- see .github/workflows/render-status-png.yml
"""

import json
import sys
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

# --- Green-phosphor theme (matches widget.html's ?theme=green) ---
BG = (20, 17, 13)
PANEL = (28, 23, 18)
FG = (57, 255, 136)
FG_DIM = (13, 92, 51)
FG_MUTED = (46, 138, 90)
GRID = (57, 255, 136, 18)  # low-alpha for the faint background grid

WIDTH, HEIGHT = 320, 140
PADDING = 12

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/local/share/fonts/DejaVuSansMono.ttf",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
]


def load_font(candidates, size):
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Fall back to Pillow's built-in bitmap font rather than crashing --
    # ugly but functional if DejaVu isn't installed (apt install fonts-dejavu-core).
    return ImageFont.load_default()


def text_width(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def truncate_to_fit(draw, text, font, max_width):
    """Trims text with a trailing '...' until it fits max_width, same approach
    the original canvas-based tool used. Long talkgroup names in particular
    can otherwise run straight off the edge of the image."""
    if text_width(draw, text, font) <= max_width:
        return text
    ellipsis = "..."
    trimmed = text
    while trimmed and text_width(draw, trimmed + ellipsis, font) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + ellipsis) if trimmed else ellipsis


def time_ago(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "--"
    diff = datetime.now(timezone.utc) - dt
    mins = int(diff.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h ago"
    return f"{hrs // 24}d ago"


STATE_LABELS = {"on_air": "ON AIR", "monitoring": "MONITORING", "off_air": "OFF AIR"}


def render(data, out_path):
    img = Image.new("RGB", (WIDTH, HEIGHT), PANEL)
    draw = ImageDraw.Draw(img, "RGBA")

    # Faint background grid, matching the widget's CSS grid texture
    for x in range(0, WIDTH, 8):
        draw.line([(x, 0), (x, HEIGHT)], fill=GRID)
    for y in range(0, HEIGHT, 8):
        draw.line([(0, y), (WIDTH, y)], fill=GRID)

    # Border
    draw.rectangle([0, 0, WIDTH - 1, HEIGHT - 1], outline=FG_DIM)

    font_call = load_font(FONT_BOLD_CANDIDATES, 15)
    font_big = load_font(FONT_BOLD_CANDIDATES, 24)
    font_small = load_font(FONT_CANDIDATES, 11)
    font_tiny = load_font(FONT_CANDIDATES, 10)

    state = data.get("state", "off_air")
    active = state in ("on_air", "monitoring")

    # Row 1: callsign + signal meter
    draw.text((PADDING, PADDING - 2), data.get("callsign", "--"), font=font_call, fill=FG)

    bar_color = FG if active else FG_DIM
    bar_x = WIDTH - PADDING - 5 * 6
    heights = [4, 7, 9, 11, 13]
    for i, h in enumerate(heights):
        x = bar_x + i * 6
        draw.rectangle([x, PADDING + 14 - h, x + 4, PADDING + 14], fill=bar_color)

    # Row 2: frequency OR talkgroup (mirrors widget.html's toggle logic)
    freq = data.get("frequency") or {}
    mode = data.get("mode", "")
    talkgroup = data.get("talkgroup")
    y2 = PADDING + 28

    if mode == "DMR" and talkgroup:
        tg_name = data.get("talkgroup_name")
        label = f"TG {talkgroup}" if (not tg_name or tg_name == str(talkgroup)) else f"TG {talkgroup} - {tg_name}"
        label = truncate_to_fit(draw, label, font_big, WIDTH - 2 * PADDING)
        draw.text((PADDING, y2), label, font=font_big, fill=FG)
    else:
        value = freq.get("value", "--")
        unit = freq.get("unit", "MHz")
        draw.text((PADDING, y2), f"{value}", font=font_big, fill=FG)
        vw = text_width(draw, f"{value}", font_big)
        draw.text((PADDING + vw + 6, y2 + 8), unit, font=font_tiny, fill=FG_MUTED)

    # Small mode/band tags, right-aligned under the meter
    tag_y = y2 + 6
    tags = [t for t in (mode, freq.get("band")) if t]
    tx = WIDTH - PADDING
    for tag in reversed(tags):
        tw = text_width(draw, tag, font_tiny) + 10
        tx -= tw
        draw.rectangle([tx, tag_y, tx + tw - 4, tag_y + 16], outline=FG_DIM)
        draw.text((tx + 5, tag_y + 2), tag, font=font_tiny, fill=FG_MUTED)
        tx -= 4

    # Row 3: activity
    activity = truncate_to_fit(draw, data.get("activity", ""), font_small, WIDTH - 2 * PADDING)
    draw.text((PADDING, y2 + 32), activity, font=font_small, fill=FG_MUTED)

    # Footer: state word + time ago, separated by a thin rule
    footer_y = HEIGHT - 22
    draw.line([(PADDING, footer_y), (WIDTH - PADDING, footer_y)], fill=FG_DIM)
    label = STATE_LABELS.get(state, state.upper())
    label_color = FG if state == "on_air" else FG_MUTED
    draw.text((PADDING, footer_y + 5), label, font=font_tiny, fill=label_color)

    ago = time_ago(data.get("last_updated", ""))
    ago_w = text_width(draw, ago, font_tiny)
    draw.text((WIDTH - PADDING - ago_w, footer_y + 5), ago, font=font_tiny, fill=FG_MUTED)

    img.save(out_path, "PNG")


if __name__ == "__main__":
    in_path = sys.argv[1] if len(sys.argv) > 1 else "status.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "status.png"
    with open(in_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    render(data, out_path)
    print(f"Rendered {out_path} from {in_path} (state={data.get('state')})")

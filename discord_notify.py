"""
HamStatus Discord Notifier

Polls a list of members' status.json files and posts to a Discord webhook
whenever someone transitions INTO "on_air" -- not every state change, just
the "someone's actually active now" signal, to keep a club channel from
getting spammed by monitoring/off_air flicker.

Needs no dedicated hosting -- designed to run as a scheduled GitHub Action
(see .github/workflows/discord-notify.yml), which is free and requires no
server anyone has to maintain.

Config:
    discord_members.json (committed, public -- just callsigns + public URLs)
    DISCORD_WEBHOOK_URL (environment variable / GitHub Actions secret --
        NEVER commit this to the repo; anyone with it can post to the
        channel). Use a DIFFERENT webhook/channel than any instant
        per-station notifier (e.g. the Pi's hamstatus_rf_service.py) to
        avoid posting the same on-air transition twice.

State is tracked in discord_state_cache.json, which the Action commits back
to the repo after each run so it persists between runs (GitHub-hosted
runners are ephemeral and remember nothing on their own).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

MEMBERS_PATH = "discord_members.json"
CACHE_PATH = "discord_state_cache.json"


def fetch_status(url):
    """Fetches a member's status.json with cache-busting, same approach
    used by the website widgets to defeat GitHub Pages' CDN cache."""
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}t={int(time.time())}",
                                  headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def build_embed(callsign, data):
    """Builds a Discord embed describing the current on-air session, using
    whatever detail is actually present (DMR talkgroup, plain frequency, or
    just the free-text activity) without assuming any one is available."""
    talkgroup_name = data.get("talkgroup_name")
    network = data.get("network")
    freq = data.get("frequency")

    if talkgroup_name and network:
        detail = f"{network} — {talkgroup_name}"
    elif isinstance(freq, dict) and freq.get("value") not in (None, ""):
        detail = f"{freq['value']} {freq.get('unit', 'MHz')}"
    else:
        detail = data.get("activity") or "—"

    embed = {
        "title": f"\U0001F534 {callsign} is ON AIR",
        "description": detail,
        "color": 0x2ecc71,
    }
    if data.get("custom_message"):
        embed["footer"] = {"text": data["custom_message"]}
    return embed


def post_webhook(webhook_url, embed):
    body = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            # Discord/Cloudflare blocks urllib's default User-Agent outright
            # (403) -- without this override every post silently fails.
            "User-Agent": "Mozilla/5.0 (compatible; hamstatus-discord-notify/1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def load_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_cache(cache):
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("[error] DISCORD_WEBHOOK_URL is not set -- nothing to post to")
        sys.exit(1)

    with open(MEMBERS_PATH, "r", encoding="utf-8") as f:
        members = json.load(f)["members"]

    cache = load_cache()

    for member in members:
        callsign = member["callsign"]
        try:
            data = fetch_status(member["status_url"])
        except (urllib.error.URLError, json.JSONDecodeError) as e:
            print(f"[error] failed to fetch {callsign}: {e}")
            continue

        state = data.get("state", "off_air")
        was_on_air = cache.get(callsign) == "on_air"

        if state == "on_air" and not was_on_air:
            try:
                post_webhook(webhook_url, build_embed(callsign, data))
                print(f"[notified] {callsign} is now on_air")
            except urllib.error.URLError as e:
                print(f"[error] failed to post webhook for {callsign}: {e}")

        cache[callsign] = state

    save_cache(cache)


if __name__ == "__main__":
    main()

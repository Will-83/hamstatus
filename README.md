# HamStatus 🟢

> **"One status, many outputs."**

HamStatus is an open-source, lightweight live presence platform designed specifically for amateur radio operators. 

Instead of manually updating your QRZ bio, personal website, and club rosters every time you change bands or go portable, HamStatus serves as your **single source of truth**. You update your status once — by hand, or automatically as your radio detects activity — and every embed reading from it updates together.

---

## ─── The Core Philosophy ───

HamStatus is **not** another logging program. Excellent loggers already exist. Instead, HamStatus aims to be a central hub that ties your existing station tools, websites, and public profiles together in real time.

**Right now, HamStatus is a single-user reference implementation** — proof that the model works, running on one real station (mine). There's no central server yet; each ham's status lives in their own `status.json`, hosted in their own GitHub repo. That file is the actual API — anything that can fetch a URL can read it.

```
           ┌──────────────────────────┐
           │      status.json          │
           │  (your own GitHub repo)   │
           └─────────────┬──────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
  [ How it gets updated ]         [ Where it shows up ]
  • admin.html (manual, in-browser) • widget.html (live iframe embed)
  • RF watcher service              • Embedded cards (any page you control)
    (DMR, TGIF, DMR2YSF via         • status.png (auto-rendered image,
     your own hotspot's local log)    for platforms that block iframes/JS,
  • APRS watcher service              e.g. QRZ bios)
    (reads APRS-IS directly)
  
  Both watcher services run on the operator's own hardware
  (e.g. a Pi-Star/WPSD hotspot) — no shared infrastructure required.
```

**The long-term plan** is to turn this into an actual multi-user platform: a real backend with a database, one record per registered callsign, a batch lookup API ("give me status for these 5 callsigns") that serves both personal use ("is my friend on the air?") and club rosters, plus tiered privacy (basic info public, more detail available only to viewers a station owner has approved). That piece doesn't exist yet — the single-station version above is deliberately proven out first, on real hardware, before that investment happens. See the Roadmap section for where things stand.

---

## ─── Features ───

### Built and working

**Dynamic QRZ Image Generator** — `status.png` auto-regenerates via a GitHub Action every time `status.json` changes, for embedding in your QRZ.com bio (or anywhere else that blocks scripts and sandboxed iframes).

**Embeddable Widget** — a live iframe (`widget.html`) that fetches your status directly, for any page that allows iframes — your personal site, a club page, anywhere.

**Embeddable Cards** — the same data rendered directly into a page's own HTML/JS, for sites you fully control and want a native look on.

**Automated DMR detection** — a watcher service tails your own hotspot's local log and updates your status the moment you key up, correctly identifying BrandMeister, TGIF, and DMR2YSF (each has its own talkgroup-numbering and name-lookup quirks — see the code for the gory details) and reverting through a monitoring window back to off-air after a period of silence.

**Automated APRS tracking** — a second watcher reads APRS-IS directly for your callsign's beacons and messages. Coordinates are always rounded to roughly 1km precision before being written, regardless of how precise the source beacon was, so exact location never touches the public file.

**Last Heard** — a persisted snapshot of your most recent DMR session (talkgroup, network, time), so that info isn't just lost the moment you go off-air.

### Planned, not yet built

- **Specialized operating-mode layouts** — dedicated POTA/SOTA, Field Day, and ARES/Skywarn presentations are an idea, not implemented. Today there's a general-purpose `state`/`activity`/`custom_message` model that can describe any of these informally, but no purpose-built layout for them yet.
- **Club Active Roster widget** — showing which of a club's members are currently active. This depends on the multi-user backend below; a single `status.json` per station doesn't have a concept of "club" at all yet.
- **Discord bot**

---

## ─── Technical Architecture ───

There's no REST API today — `status.json` is a plain static file, fetched with a normal `GET` request to wherever it's hosted (typically `https://<your-username>.github.io/<repo>/status.json`, since GitHub Pages serves it for free with no traffic limits that matter here). Every widget, card, and the PNG renderer all just fetch that one file. The schema is versioned (`"version": "1.0"`) specifically so it can evolve without breaking everything reading it at once.

### Real Sample Payload

This is what `status.json` actually contains today — captured from a live station mid-QSO, not a hypothetical:

```json
{
  "version": "1.0",
  "callsign": "W8MB",
  "state": "on_air",
  "activity": "US-America Link",
  "frequency": {
    "value": 147.09,
    "unit": "MHz",
    "band": "2m"
  },
  "mode": "DMR",
  "location": {
    "description": "Mansfield, MO",
    "grid_square": "EM37",
    "reference": "Local Repeater"
  },
  "custom_message": "Transmitting on US-America Link",
  "last_updated": "2026-07-19T15:59:01Z",
  "talkgroup": 32592,
  "talkgroup_name": "US-America Link",
  "network": "DMR2YSF",
  "aprs": {
    "last_beacon": "2026-07-19T15:54:17Z",
    "position": { "lat": 37.11, "lon": -92.58 },
    "symbol": "/a",
    "course": 0,
    "speed_mph": 0,
    "altitude_ft": null,
    "comment": "William",
    "last_message": { "to": "W8MB", "text": "Test message{2", "time": "2026-07-19T02:55:17Z" }
  },
  "last_heard": {
    "mode": "DMR",
    "talkgroup": 31665,
    "talkgroup_name": "TGIF The Mothership",
    "network": "TGIF",
    "time": "2026-07-19T16:08:39Z"
  }
}
```

A few things worth knowing about this shape:
- `state` is one of `on_air`, `monitoring`, or `off_air` — set manually via `admin.html`, or automatically by the RF watcher with a timed decay (immediately on key-up, `monitoring` after ~30s of silence, `off_air` after ~2 minutes)
- `talkgroup`/`talkgroup_name`/`network` only appear while a DMR session is actually active or in its monitoring window — they're absent entirely once truly `off_air`
- `aprs` and `last_heard` are both optional, additive sections — a station with neither watcher running simply won't have those keys at all, and every reader is expected to handle their absence gracefully
- Location is intentionally coarse — no precise coordinates are ever written, even when the source data (like a DM-32's GPS) was precise

---

## ─── Roadmap ───

**Working today**, on a single real station:
- `status.json` as a versioned, documented schema (state, activity, frequency, mode, location, talkgroup/network, custom message)
- Manual updates via `admin.html`
- Automated DMR detection (BrandMeister, TGIF, DMR2YSF) via a local RF-watcher service
- Automated APRS beaconing/messaging via a local watcher reading APRS-IS directly, with coordinates always rounded for privacy
- Live embeddable widget (iframe), embeddable page cards, and an auto-rendered PNG for platforms that block scripts/iframes (QRZ bios)

**Planned:**
- Multi-user backend (real database, one record per registered callsign)
- Batch lookup API for "check these callsigns" and club rosters
- Tiered privacy (public basic info; more detail available only to approved viewers)
- Club-curated rosters with independent per-member opt-out of display
- Discord bot
- Beta testing with a small group once the multi-user version exists
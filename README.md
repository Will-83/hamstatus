# HamStatus 🟢

> **"One status, many outputs."**

HamStatus is an open-source, lightweight live presence platform designed specifically for amateur radio operators. 

Instead of manually updating your QRZ bio, personal website, Discord status, and club rosters every time you change bands or go portable, HamStatus serves as your **single source of truth**. You update your status in one place (manually or via API), and HamStatus instantly broadcasts it everywhere.

---

## ─── The Core Philosophy ───

HamStatus is **not** another logging program. Excellent loggers already exist. Instead, HamStatus aims to be the "Home Assistant" of amateur radio presence—a central hub that ties your existing station tools, websites, and public profiles together in real time.

           ┌────────────────────────┐
           │     HamStatus Core     │
           │   (Central API & DB)   │
           └───────────┬────────────┘
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
  [ Input Methods ]           [ Output Deliverables ]
 • Web Dashboard             • Dynamic QRZ Images (.png)
 • REST API / JSON           • Embeddable HTML Widgets
 • Station Logger Hook       • JSON API for Custom Sites
                             • Discord / Matrix Bots

---

## ─── Features ───

### 1. Dynamic QRZ Image Generator
Generate a clean, dynamically rendered image (e.g., `status.png`) to embed directly into your QRZ.com biography. It updates automatically the moment you change your status, showing your live frequency, mode, and operating state.

### 2. Embeddable Widgets
*   **Personal Badge:** A tiny, cut-and-paste HTML iframe for your personal blog or GitHub Pages site.
*   **Club Active Roster:** A live widget for club websites showing which members are currently active, monitoring, or operating portable.

### 3. Specialized Operating Modes
*   🟢 **Standard:** "Monitoring 146.940 MHz" or "Chasing DX on 20m SSB."
*   🏕️ **Event/Portable:** Optimized layouts for **POTA/SOTA** activations, **Field Day**, or Special Event stations (complete with frequency, park designator, and location details).
*   ⚠️ **Emergency/Skywarn:** A high-visibility mode for ARES/RACES and Skywarn spotters to display active net frequencies, Net Control status, and local tactical information.

---

## ─── Technical Architecture ───

At its core, HamStatus consumes and serves structured JSON. The platform is built to handle quick read requests so your widgets and QRZ profiles load instantly.

### Sample Status Payload (`GET /api/v1/status/YOUR_CALLSIGN`)

```json
{
  "callsign": "K0ABC",
  "is_active": true,
  "operating_mode": "POTA",
  "frequency": "14.285",
  "mode": "USB",
  "location": {
    "grid_square": "EM37",
    "description": "Mansfield, MO",
    "reference": "K-1234 (POTA)"
  },
  "last_updated": "2026-07-17T11:45:00Z",
  "custom_message": "Chasing the last few contacts before UTC rollup!"
}

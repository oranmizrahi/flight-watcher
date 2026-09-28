# Flight watcher

Watches flight-search result pages in a real browser and sends a **Telegram** alert
only when something matters. Runs on GitHub Actions (public repo = free minutes)
or on your own machine.

## What it does
- **Smart alerts** – cheaper fare (📉), new flight (➕), flight gone (➖), or price under your
  `alert_below` threshold (🎯). Price *rises* are recorded, and shown only when an alert fires anyway.
- **Says what changed** – per flight, e.g. `📉 ירד: Swiss 20:25 (עצירה אחת, 19:35) $520 ← $431`,
  plus since which check, the cheapest fare now, the lowest ever seen and a small trend chart `▁▃█`.
- **Price history** – `data/history.json` (min price per run, last 800 runs).
- **Neighbour dates** – `date_offsets` watches the days around your date (SkyGini URLs).
- **Health alerts** – a page failing 3 runs in a row sends one ⚠️ warning, then a ✅ when it recovers.
- **Daily heartbeat** – one 💓 message a day (~09:00 Israel) with the current cheapest fares.
- **Button** – each alert has an inline "🔗 open results" button.

## Setup
1. Telegram: @BotFather -> `/newbot` -> token. Message your bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
2. Repo -> Settings -> Secrets and variables -> Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. Actions -> flight-watch -> Run workflow.

## targets.json
```json
{
  "name": "SkyGini · קטניה→תל אביב 30.09",
  "url": "https://www.skygini.com/search-v2/2026-09-30_...",
  "start_marker": "טיסות עבורך",
  "ignore": ["עוגיות", "קבל הכל"],
  "date_offsets": [-1, 0, 1, 2],
  "alert_on": ["drop", "new", "gone"],
  "alert_below": 400,
  "enabled": true
}
```
| key | meaning |
|---|---|
| `url` | a **results** URL (run the search in your browser, copy the address bar) |
| `start_marker` | keep only the page from this line on (drops nav/footer noise) |
| `ignore` | drop lines containing these strings (cookie banners...) |
| `date_offsets` | also watch date ± N days (replaces the `YYYY-MM-DD` in the URL) |
| `alert_on` | any of `drop`, `new`, `gone`, `rise`, `change` |
| `alert_below` | alert when the cheapest fare goes under this number (once per dip) |
| `search` | form-filling for El Al style pages (see the El Al note) |
| `enabled` | `false` skips the target |

## How it gets past Cloudflare
[patchright](https://pypi.org/project/patchright/) (undetected Playwright) + **headful** browser
under a virtual display (xvfb). Plain/headless Playwright gets the "verify you are human" page and
sees zero prices.

## Run it on your own machine (WSL/Linux, no sudo)
```bash
scripts/local-setup.sh          # once: venv, Chromium, private Xvfb
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
scripts/run-local.sh            # DRY_RUN=1 to only print; ONLY_TARGET="[01.10]" for one target
```
A local run is also how to get an **Israeli IP**, which El Al needs.

## El Al
`booking.elal.com` geo-redirects: from GitHub's US runners you get the English/New York site or a
marketing page (whose promo prices used to look like fares). The target is `enabled:false`
by default. With an Israeli IP set `enabled:true` and use a route El Al actually flies
(from Catania it had **no flights on 28-30 Sep**, first fares 5 Oct). A target with `search`
that can't reach the form counts as a *failure*, not as "prices found".

## Cost / limits
Runs take a few minutes each, every 10 min. Public repo: Actions minutes are free.
Private repo: 2,000 free min/month, so slow the cron or run it locally.

## Debug
Each run uploads `last-<target>.png/.txt` (Actions -> run -> Artifacts): exactly what the browser saw.

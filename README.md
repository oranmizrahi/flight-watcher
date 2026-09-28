# Flight watcher

Loads flight-search result pages in a real browser, snapshots the **flight cards**,
and sends a Telegram message on ANY change (price, flight added/removed, times).

Runs on GitHub Actions. Cloudflare's bot wall is handled with
[patchright](https://pypi.org/project/patchright/) (an undetected Playwright build)
driving **real Chrome, headful, under xvfb** — plain Playwright gets the
"ביצוע אימות אבטחה" interstitial on GitHub runners and sees zero prices.

## Setup
1. **Telegram bot**: in Telegram open @BotFather -> `/newbot` -> copy the token.
   Send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
2. Repo -> Settings -> Secrets and variables -> Actions -> add
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. Actions tab -> flight-watch -> Run workflow. You should get a "✅ מעקב הופעל" message.

## Watching more pages
Everything lives in `targets.json`:

```json
{
  "name": "SkyGini · קטניה→תל אביב 30.09",
  "url": "https://www.skygini.com/search-v2/...",
  "state": "state.json",
  "start_marker": "טיסות עבורך"
}
```

- `url` must be a **results** URL (run the search in your browser, then copy the address bar).
  A bare booking homepage is just an empty search form and yields no prices.
- `start_marker` trims the snapshot to the results section, so nav/footer/banner
  churn doesn't trigger alerts. Set it to `null` to diff the whole page.
- `state` is the per-target snapshot file, committed back by the workflow.

## Cost warning
Each run takes ~3-4 minutes (browser install + two pages). At the default
10-minute cron that is roughly **16,000 Actions minutes/month**:

- **Public repo** -> Actions minutes are free and unlimited. Secrets stay encrypted.
- **Private repo** -> only 2,000 free minutes/month; the rest is billed.
  Either raise the cron interval a lot, or run it on your own VM.

## Debugging
Every run uploads `last-<target>.png` / `last-<target>.txt` (Actions -> run -> Artifacts)
showing exactly what the browser saw — including a bot wall, if one appears.

## Running locally (WSL)
```bash
pip install -r requirements.txt
patchright install --with-deps chromium && patchright install chrome
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
xvfb-run -a python watch.py          # or drop xvfb-run if you have a display
ONLY_TARGET=SkyGini python watch.py  # just one target
```

"""Flight watcher: loads the SkyGini results page in a real browser,
snapshots the results, diffs against the previous snapshot and alerts on ANY change."""
import json, os, re, sys, difflib, urllib.request, urllib.parse
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = os.environ.get("WATCH_URL",
    "https://www.skygini.com/search-v2/2026-09-30_NULL_C-CTA_C-TLV_A2_S0_C0_I0_CL1_NOTDIRECT")
STATE = "state.json"
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
PRICE_RE = re.compile(r"[₪$€]\s?\d[\d,\.]*|\d[\d,\.]*\s?[₪$€]")


def fetch_snapshot():
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="he-IL", viewport={"width": 1400, "height": 2200})
        pg = ctx.new_page()
        pg.goto(URL, timeout=60000)
        # accept cookie banner if present
        for label in ("קבל הכל",):
            try:
                pg.get_by_text(label, exact=True).click(timeout=4000)
            except Exception:
                pass
        # wait until prices appear (results are loaded by JS), up to ~90s
        try:
            pg.wait_for_function(
                "document.body.innerText.match(/[₪$€]\\s?\\d|\\d\\s?[₪$€]/)", timeout=90000)
            pg.wait_for_timeout(8000)  # let the list finish loading/sorting
        except Exception:
            print("warning: no prices detected", file=sys.stderr)
        text = pg.inner_text("body")
        pg.screenshot(path="last.png", full_page=True)
        open("last.txt", "w", encoding="utf-8").write(text)
        b.close()
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    return lines


def send(msg):
    print(msg)
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram not configured", file=sys.stderr)
        return
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=30)


def main():
    lines = fetch_snapshot()
    prices = PRICE_RE.findall("\n".join(lines))
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not prices:
        # page failed to load results - don't overwrite good state, don't spam
        print("No results parsed; skipping compare.")
        sys.exit(0)

    old = None
    if os.path.exists(STATE):
        old = json.load(open(STATE, encoding="utf-8"))

    new = {"updated": now, "lines": lines}
    if old is None:
        send(f"✅ הבוט פעיל. נמצאו {len(prices)} מחירים בדף. מעכשיו אקבל התראה על כל שינוי.\n{URL}")
    elif old["lines"] != lines:
        diff = [d for d in difflib.unified_diff(old["lines"], lines, lineterm="", n=0)
                if d[:1] in "+-" and d[:3] not in ("+++", "---")]
        body = "\n".join(diff[:40])
        send(f"🔔 זוהה שינוי בטיסות!\n{body}\n\n{URL}")
    else:
        print("No change.")
    json.dump(new, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

"""Flight watcher: loads the SkyGini results page in a real browser,
snapshots the results, diffs against the previous snapshot and alerts on ANY change."""
import json, os, re, sys, difflib, urllib.request, urllib.parse
from datetime import datetime, timezone

# patchright is a drop-in Playwright that doesn't trip Cloudflare's bot check;
# plain playwright stays as a fallback for local runs.
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

URL = os.environ.get("WATCH_URL",
    "https://www.skygini.com/search-v2/2026-09-30_NULL_C-CTA_C-TLV_A2_S0_C0_I0_CL1_NOTDIRECT")
STATE = "state.json"
PROFILE = os.environ.get("BROWSER_PROFILE", "/tmp/skygini-profile")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
PRICE_RE = re.compile(r"[₪$€]\s?\d[\d,\.]*|\d[\d,\.]*\s?[₪$€]")
HAS_PRICE_JS = "document.body.innerText.match(/[₪$€]\\s?\\d|\\d\\s?[₪$€]/)"
# Cloudflare interstitial markers (Hebrew + English)
CHALLENGE = ("אימות אבטחה", "Just a moment", "Ray ID", "Checking your browser",
             "Verifying you are human", "בוט זדוני")


def is_challenge(text):
    return any(m in text for m in CHALLENGE) and not PRICE_RE.search(text)


def fetch_snapshot():
    with sync_playwright() as p:
        # persistent context + real Chrome + headful: the combination Cloudflare
        # treats as a normal visitor. Do NOT add a custom UA here, it breaks the
        # fingerprint and gets the challenge back.
        kwargs = dict(user_data_dir=PROFILE, headless=False, no_viewport=True,
                      locale="he-IL", timezone_id="Asia/Jerusalem")
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception as e:
            print(f"chrome channel unavailable ({e}); using bundled chromium", file=sys.stderr)
            ctx = p.chromium.launch_persistent_context(**kwargs)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto(URL, timeout=90000, wait_until="domcontentloaded")

        # sit through the Cloudflare interstitial if we got one
        for _ in range(12):
            if not is_challenge(pg.inner_text("body")):
                break
            pg.wait_for_timeout(5000)

        for label in ("קבל הכל",):
            try:
                pg.get_by_text(label, exact=True).click(timeout=4000)
            except Exception:
                pass
        try:
            pg.wait_for_function(HAS_PRICE_JS, timeout=120000)
            pg.wait_for_timeout(8000)  # let the list finish loading/sorting
        except Exception:
            print("warning: no prices detected", file=sys.stderr)
        text = pg.inner_text("body")
        pg.screenshot(path="last.png", full_page=True)
        open("last.txt", "w", encoding="utf-8").write(text)
        ctx.close()
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    return [l for l in lines if l]


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
    joined = "\n".join(lines)
    prices = PRICE_RE.findall(joined)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not prices:
        # page failed to load results - don't overwrite good state, don't spam
        why = "blocked by Cloudflare" if is_challenge(joined) else "no prices on page"
        print(f"No results parsed ({why}); skipping compare.")
        sys.exit(0)

    old = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else None
    new = {"updated": now, "lines": lines}
    if old is None:
        send(f"✅ הבוט פעיל. נמצאו {len(prices)} מחירים בדף. מעכשיו אקבל התראה על כל שינוי.\n{URL}")
    elif old["lines"] != lines:
        diff = [d for d in difflib.unified_diff(old["lines"], lines, lineterm="", n=0)
                if d[:1] in "+-" and d[:3] not in ("+++", "---")]
        send(f"🔔 זוהה שינוי בטיסות!\n" + "\n".join(diff[:40]) + f"\n\n{URL}")
    else:
        print("No change.")
    json.dump(new, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

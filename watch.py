"""Flight watcher: loads each target's results page in a real browser, snapshots
the flight cards, diffs against the previous snapshot and alerts on ANY change."""
import json, os, re, sys, difflib, urllib.request, urllib.parse
from datetime import datetime, timezone

# patchright is a drop-in Playwright that doesn't trip Cloudflare's bot check;
# plain playwright stays as a fallback for local runs.
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

TARGETS = os.environ.get("TARGETS_FILE", "targets.json")
ONLY = os.environ.get("ONLY_TARGET")          # substring filter, for manual runs
PROFILE = os.environ.get("BROWSER_PROFILE", "/tmp/watch-profile")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
PRICE_RE = re.compile(r"[₪$€]\s?\d[\d,\.]*|\d[\d,\.]*\s?[₪$€]")
HAS_PRICE_JS = "document.body.innerText.match(/[₪$€]\\s?\\d|\\d\\s?[₪$€]/)"
# Cloudflare / bot-wall interstitial markers (Hebrew + English)
CHALLENGE = ("אימות אבטחה", "Just a moment", "Ray ID", "Checking your browser",
             "Verifying you are human", "בוט זדוני")


def is_challenge(text):
    return any(m in text for m in CHALLENGE) and not PRICE_RE.search(text)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "target"


def pick_place(pg, input_id, query, code):
    """Type into an airport autocomplete and choose the option matching `code`."""
    pg.locator(f"#{input_id}").click()
    pg.keyboard.press("Control+A")
    pg.keyboard.type(query, delay=80)
    pg.wait_for_timeout(2500)
    opt = pg.get_by_text(re.compile(rf"\b{code}\b")).first
    opt.click(timeout=8000)
    pg.wait_for_timeout(800)


def run_search(pg, sp):
    """Fill El Al's search form from targets.json 'search' block."""
    y, m, d = (int(x) for x in sp["date"].split("-"))
    pg.wait_for_timeout(6000)
    if sp.get("one_way", True):
        pg.get_by_text(re.compile(r"^(כיוון אחד|One way)$")).first.click(timeout=8000)
        pg.wait_for_timeout(800)
    if sp.get("origin"):
        pick_place(pg, "outbound-origin-location-input", sp["origin"][0], sp["origin"][1])
    pick_place(pg, "outbound-destination-location-input", sp["destination"][0], sp["destination"][1])
    # date: open calendar, jump to the month tab, click the day
    pg.locator("#outbound-departure\\,return-calendar-input").click()
    pg.wait_for_timeout(1500)
    months = [r"ינו|Jan", r"פבר|Feb", r"מרץ|Mar", r"אפר|Apr", r"מאי|May", r"יונ|Jun",
              r"יול|Jul", r"אוג|Aug", r"ספט|Sep", r"אוק|Oct", r"נוב|Nov", r"דצמ|Dec"]
    try:
        pg.get_by_text(re.compile(rf"^({months[m - 1]})\.?$")).first.click(timeout=3000)
        pg.wait_for_timeout(800)
    except Exception:
        pass
    # two months are shown side by side (RTL: first month = right panel = largest x)
    cells = pg.get_by_text(str(d), exact=True).locator("visible=true")
    boxes = [(cells.nth(i).bounding_box() or {"x": -1}, i) for i in range(cells.count())]
    cells.nth(max(boxes, key=lambda b: b[0]["x"])[1]).click(timeout=8000)
    pg.locator('button[aria-label="search.calendar.submit"]').first.click(force=True, timeout=5000)
    pg.wait_for_timeout(800)
    # passengers (best effort: on failure fall back to 1 adult so the search still runs)
    if sp.get("adults", 1) > 1:
        try:
            pg.locator("#passenger-counters-input").click(force=True)
            pg.wait_for_timeout(1500)
            for _ in range(sp["adults"] - 1):
                pg.locator("#ADT-add").click(timeout=4000)
            pg.mouse.click(700, 600)  # click away to close the popup
            pg.wait_for_timeout(800)
        except Exception as e:
            print(f"  passengers not set ({str(e)[:120]}); continuing with 1", file=sys.stderr)
    pg.screenshot(path=f"last-{slug(sp.get('_name', 'form'))}-form.png", full_page=True)
    pg.get_by_role("button", name=re.compile("חיפוש טיסה")).first.click()


def fetch(pg, t):
    """Load one target and return its visible lines."""
    pg.goto(t["url"], timeout=90000, wait_until="domcontentloaded")
    if t.get("search"):
        t["search"]["_name"] = t["name"]
        try:
            run_search(pg, t["search"])
        except Exception as e:
            print(f"  search automation failed on {t['name']}: {str(e)[:300]}", file=sys.stderr)

    # sit through the Cloudflare interstitial if we got one
    for _ in range(12):
        if not is_challenge(pg.inner_text("body")):
            break
        pg.wait_for_timeout(5000)

    for label in ("קבל הכל", "אישור", "Accept All"):
        try:
            pg.get_by_text(label, exact=True).first.click(timeout=3000)
        except Exception:
            pass
    try:
        pg.wait_for_function(HAS_PRICE_JS, timeout=120000)
        pg.wait_for_timeout(8000)  # let the list finish loading/sorting
    except Exception:
        print(f"  warning: no prices detected on {t['name']}", file=sys.stderr)

    text = pg.inner_text("body")
    s = slug(t["name"])
    pg.screenshot(path=f"last-{s}.png", full_page=True)
    open(f"last-{s}.txt", "w", encoding="utf-8").write(text)

    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    # keep the results section only, so nav/footer/banner churn doesn't alert
    marker = t.get("start_marker")
    if marker:
        hit = next((i for i, l in enumerate(lines) if marker in l), None)
        if hit is not None:
            lines = lines[hit:]
        else:
            print(f"  note: start_marker not found on {t['name']}, keeping whole page")
    ignore = t.get("ignore", [])
    if ignore:
        lines = [l for l in lines if not any(x in l for x in ignore)]
    return lines


def send(msg):
    print(msg)
    if not (TG_TOKEN and TG_CHAT):
        print("Telegram not configured", file=sys.stderr)
        return
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": msg[:4000],
                                   "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=30)


SOLO_PRICE = re.compile(r"^[₪$€]\s?\d[\d,\.]*$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def parse_cards(lines):
    """Group lines into flight cards -> {(airline, dep, duration, stops): [prices]}.
    A card starts at the airline line (the line before the 'NKG' baggage line)
    and ends at its 'בחר טיסה' line; works for SkyGini-style result lists."""
    cards, cur = {}, None
    for i, l in enumerate(lines):
        if i + 1 < len(lines) and "KG" in lines[i + 1] and "KG" not in l and cur is None:
            cur = [l]
            continue
        if cur is None:
            continue
        cur.append(l)
        if "בחר טיסה" in l:
            times = [x for x in cur if TIME_RE.match(x)]
            dur = next((x for x in cur if re.match(r"^\d{1,2}:\d{2} ש", x)), "")
            stops = next((x for x in cur if "עצירה" in x or "ישיר" in x), "")
            price = next((x for x in cur if SOLO_PRICE.match(x)), None)
            if price and times:
                cards.setdefault((cur[0], times[0], dur.split(" ")[0], stops), []).append(price)
            cur = None
    return cards


def num(p):
    return float(re.sub(r"[^\d.]", "", p.replace(",", "")) or 0)


def summarize(old_lines, new_lines):
    """Human summary of what changed between two snapshots, one line per flight."""
    o, n = parse_cards(old_lines), parse_cards(new_lines)
    out = []
    for k in sorted(set(o) | set(n)):
        label = f"{k[0]} {k[1]} ({k[3] or 'ישיר'}, {k[2]})"
        a, b = sorted(o.get(k, []), key=num), sorted(n.get(k, []), key=num)
        if a == b:
            continue
        if not a:
            out.append(f"➕ טיסה חדשה: {label} — {', '.join(b)}")
        elif not b:
            out.append(f"➖ הטיסה נעלמה: {label} (היה {', '.join(a)})")
        elif len(a) == len(b):
            for x, y in zip(a, b):
                if x != y:
                    out.append(f"{'📉 ירד' if num(y) < num(x) else '📈 עלה'}: {label} {x} ← {y}")
        else:
            out.append(f"🔀 {label}: היה {', '.join(a)} | עכשיו {', '.join(b)}")
    return out


def compare(t, lines):
    joined = "\n".join(lines)
    prices = PRICE_RE.findall(joined)
    if not prices:
        # page failed to load results - don't overwrite good state, don't spam
        why = "blocked by bot wall" if is_challenge(joined) else "no prices on page"
        print(f"  {t['name']}: no results parsed ({why}); skipping compare.")
        return False

    path = t["state"]
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else None
    new = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "url": t["url"], "lines": lines}
    if old is None:
        send(f"✅ מעקב הופעל: {t['name']}\nנמצאו {len(prices)} מחירים. מעכשיו תגיע התראה על כל שינוי.\n{t['url']}")
    elif old.get("lines") != lines:
        diff = [d for d in difflib.unified_diff(old.get("lines", []), lines, lineterm="", n=0)
                if d[:1] in "+-" and d[:3] not in ("+++", "---")]
        summary = summarize(old.get("lines", []), lines)
        since = old.get("updated", "?").replace("T", " ")[:16]
        head = f"🔔 שינוי ב-{t['name']}\n(מאז הבדיקה ב-{since} UTC)\n"
        if summary:
            body = "\n".join(summary[:15])
        else:
            body = "לא זוהו שינויי מחיר/טיסות בכרטיסים; שינוי בטקסט הדף:\n" + "\n".join(diff[:12])
        send(head + body + f"\n\n{t['url']}")
    else:
        print(f"  {t['name']}: no change.")
    json.dump(new, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return True


def main():
    targets = json.load(open(TARGETS, encoding="utf-8"))
    if ONLY:
        targets = [t for t in targets if ONLY in t["name"]]
    ok = 0
    with sync_playwright() as p:
        # persistent context + real Chrome + headful: the combination Cloudflare
        # treats as a normal visitor. Do NOT add a custom UA here, it breaks the
        # fingerprint and gets the challenge back.
        kwargs = dict(user_data_dir=PROFILE, headless=os.environ.get("HEADLESS") == "1", no_viewport=True,
                      locale="he-IL", timezone_id="Asia/Jerusalem")
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception as e:
            print(f"chrome channel unavailable ({e}); using bundled chromium", file=sys.stderr)
            ctx = p.chromium.launch_persistent_context(**kwargs)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        for t in targets:
            print(f"== {t['name']}")
            try:
                ok += compare(t, fetch(pg, t))
            except Exception as e:
                print(f"  ERROR on {t['name']}: {e}", file=sys.stderr)
        ctx.close()
    print(f"{ok}/{len(targets)} targets compared.")


if __name__ == "__main__":
    main()

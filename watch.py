"""Flight watcher.

Loads flight-search result pages in a real browser, snapshots the flight cards,
and sends a Telegram message only for changes that matter (cheaper fare, new or
removed flight, price under your threshold). Also keeps a price history, watches
neighbouring dates, warns when a page keeps failing, and sends a daily heartbeat.
"""
import json, os, re, sys, difflib, hashlib, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone

# patchright is a drop-in Playwright that doesn't trip Cloudflare's bot check;
# plain playwright stays as a fallback.
try:
    from patchright.sync_api import sync_playwright
except ImportError:
    from playwright.sync_api import sync_playwright

TARGETS = os.environ.get("TARGETS_FILE", "targets.json")
DATA = os.environ.get("DATA_DIR", "data")
ONLY = os.environ.get("ONLY_TARGET")           # substring filter for manual runs
DRY = os.environ.get("DRY_RUN") == "1"         # print alerts, don't send or save
PROFILE = os.environ.get("BROWSER_PROFILE", "/tmp/watch-profile")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")
FAIL_ALERT_AFTER = int(os.environ.get("FAIL_ALERT_AFTER", "3"))
HEARTBEAT_HOUR_UTC = int(os.environ.get("HEARTBEAT_HOUR_UTC", "6"))   # 09:00 Israel
HISTORY_KEEP = 800

PRICE_RE = re.compile(r"[₪$€]\s?\d[\d,\.]*|\d[\d,\.]*\s?[₪$€]")
SOLO_PRICE = re.compile(r"^[₪$€]\s?\d[\d,\.]*$")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
HAS_PRICE_JS = "document.body.innerText.match(/[₪$€]\\s?\\d|\\d\\s?[₪$€]/)"
CHALLENGE = ("אימות אבטחה", "Just a moment", "Ray ID", "Checking your browser",
             "Verifying you are human", "בוט זדוני", "access denied")


# ---------------------------------------------------------------- helpers
def now_utc():
    return datetime.now(timezone.utc)


def is_challenge(text):
    low = text.lower()
    return any(m.lower() in low for m in CHALLENGE) and not PRICE_RE.search(text)


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    return (s + "-" if s else "") + hashlib.md5(name.encode()).hexdigest()[:6]


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    if DRY:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def num(p):
    return float(re.sub(r"[^\d.]", "", p.replace(",", "")) or 0)


def money(x, cur="$"):
    return f"{cur}{x:,.0f}"


def spark(vals):
    """Tiny text chart of a price series, e.g. ▂▃▅▁▁"""
    vals = [v for v in vals if v]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    bars = "▁▂▃▄▅▆▇█"
    return "".join(bars[0 if hi == lo else round((v - lo) / (hi - lo) * 7)] for v in vals)


# ---------------------------------------------------------------- telegram
def send(msg, url=None):
    print(msg)
    if DRY or not (TG_TOKEN and TG_CHAT):
        if not DRY:
            print("Telegram not configured", file=sys.stderr)
        return
    fields = {"chat_id": TG_CHAT, "text": msg[:4000], "disable_web_page_preview": "true"}
    if url:
        fields["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "🔗 פתח את התוצאות", "url": url}]]})
    data = urllib.parse.urlencode(fields).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=30)
    except Exception as e:
        print(f"telegram send failed: {e}", file=sys.stderr)


# ---------------------------------------------------------------- browser
def pick_place(pg, input_id, query, code):
    """Type into an airport autocomplete and choose the option matching `code`."""
    pg.locator(f"#{input_id}").click(timeout=8000)
    pg.keyboard.press("Control+A")
    pg.keyboard.type(query, delay=80)
    pg.wait_for_timeout(2500)
    pg.get_by_text(re.compile(rf"\b{code}\b")).first.click(timeout=8000)
    pg.wait_for_timeout(800)


def run_search(pg, sp):
    """Fill an El Al style search form from the target's 'search' block."""
    y, m, d = (int(x) for x in sp["date"].split("-"))
    pg.wait_for_selector("#outbound-destination-location-input", timeout=20000)
    if sp.get("one_way", True):
        pg.get_by_text(re.compile(r"^(כיוון אחד|One way)$")).first.click(timeout=8000)
        pg.wait_for_timeout(800)
    if sp.get("origin"):
        pick_place(pg, "outbound-origin-location-input", sp["origin"][0], sp["origin"][1])
    pick_place(pg, "outbound-destination-location-input", sp["destination"][0], sp["destination"][1])
    pg.locator("#outbound-departure\\,return-calendar-input").click()
    pg.wait_for_timeout(1500)
    months = [r"ינו|Jan", r"פבר|Feb", r"מרץ|Mar", r"אפר|Apr", r"מאי|May", r"יונ|Jun",
              r"יול|Jul", r"אוג|Aug", r"ספט|Sep", r"אוק|Oct", r"נוב|Nov", r"דצמ|Dec"]
    try:
        pg.get_by_text(re.compile(rf"^({months[m - 1]})\.?$")).first.click(timeout=3000)
        pg.wait_for_timeout(800)
    except Exception:
        pass
    # two months side by side (RTL: first month = right panel = largest x)
    cells = pg.get_by_text(str(d), exact=True).locator("visible=true")
    boxes = [(cells.nth(i).bounding_box() or {"x": -1}, i) for i in range(cells.count())]
    cells.nth(max(boxes, key=lambda b: b[0]["x"])[1]).click(timeout=8000)
    pg.locator('button[aria-label="search.calendar.submit"]').first.click(force=True, timeout=5000)
    pg.wait_for_timeout(800)
    if sp.get("adults", 1) > 1:
        try:
            pg.locator("#passenger-counters-input").click(force=True)
            pg.wait_for_timeout(1500)
            for _ in range(sp["adults"] - 1):
                pg.locator("#ADT-add").click(timeout=4000)
            pg.mouse.click(700, 600)
            pg.wait_for_timeout(800)
        except Exception as e:
            print(f"  passengers not set ({str(e)[:120]}); continuing with 1", file=sys.stderr)
    pg.get_by_role("button", name=re.compile("חיפוש טיסה|Search")).first.click()


def fetch(pg, t):
    """Load one target; return its visible lines. Raises if the page isn't usable."""
    pg.goto(t["url"], timeout=90000, wait_until="domcontentloaded")
    if t.get("search"):
        run_search(pg, t["search"])          # failure here = target failed (no false prices)
    for _ in range(12):                      # sit through a bot-wall interstitial
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
        pg.wait_for_timeout(8000)
    except Exception:
        print(f"  warning: no prices detected on {t['name']}", file=sys.stderr)

    text = pg.inner_text("body")
    s = t["slug"]
    pg.screenshot(path=f"last-{s}.png", full_page=True)
    open(f"last-{s}.txt", "w", encoding="utf-8").write(text)

    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    marker = t.get("start_marker")
    if marker:
        hit = next((i for i, l in enumerate(lines) if marker in l), None)
        if hit is not None:
            lines = lines[hit:]
    ignore = t.get("ignore", [])
    if ignore:
        lines = [l for l in lines if not any(x in l for x in ignore)]
    return lines


# ---------------------------------------------------------------- analysis
def parse_cards(lines):
    """-> {(airline, dep, duration, stops): [prices]} for SkyGini-style card lists.
    A card starts at the airline line (the one before the 'NKG' baggage line)
    and ends at its 'בחר טיסה' line."""
    cards, cur = {}, None
    for i, l in enumerate(lines):
        if cur is None:
            if i + 1 < len(lines) and "KG" in lines[i + 1] and "KG" not in l:
                cur = [l]
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


def all_prices(cards):
    return [num(p) for v in cards.values() for p in v]


def currency_of(cards, lines):
    for v in cards.values():
        return v[0][0]
    m = PRICE_RE.search("\n".join(lines))
    return next((c for c in "₪$€" if m and c in m.group()), "$")


def analyze(old_lines, new_lines):
    """-> list of (kind, text); kind in drop|rise|new|gone|change."""
    o, n = parse_cards(old_lines), parse_cards(new_lines)
    out = []
    for k in sorted(set(o) | set(n)):
        label = f"{k[0]} {k[1]} ({k[3] or 'ישיר'}, {k[2]})"
        a, b = sorted(o.get(k, []), key=num), sorted(n.get(k, []), key=num)
        if a == b:
            continue
        if not a:
            out.append(("new", f"➕ טיסה חדשה: {label} — {', '.join(b)}"))
        elif not b:
            out.append(("gone", f"➖ הטיסה נעלמה: {label} (היה {', '.join(a)})"))
        elif len(a) == len(b):
            for x, y in zip(a, b):
                if x != y:
                    down = num(y) < num(x)
                    out.append(("drop" if down else "rise",
                                f"{'📉 ירד' if down else '📈 עלה'}: {label} {x} ← {y}"))
        else:
            kind = "drop" if num(b[0]) < num(a[0]) else "change"
            out.append((kind, f"🔀 {label}: היה {', '.join(a)} | עכשיו {', '.join(b)}"))
    return out


# ---------------------------------------------------------------- per target
def process(t, lines, hist, status):
    """Compare with the last snapshot, decide whether to alert. Returns True if usable."""
    cards = parse_cards(lines)
    prices = all_prices(cards) or [num(p) for p in PRICE_RE.findall("\n".join(lines))]
    if not prices:
        raise RuntimeError("blocked by bot wall" if is_challenge("\n".join(lines))
                           else "no prices on page")

    path = os.path.join(DATA, f"state-{t['slug']}.json")
    old = load_json(path, None)
    cur = currency_of(cards, lines)
    cheapest = min(prices)
    t_hist = hist.setdefault(t["slug"], [])
    prev_min = t_hist[-1]["min"] if t_hist else None
    t_hist.append({"t": now_utc().isoformat(timespec="minutes"), "min": cheapest, "n": len(prices)})
    del t_hist[:-HISTORY_KEEP]
    lowest = min(h["min"] for h in t_hist)
    trend = spark([h["min"] for h in t_hist[-24:]])

    footer = f"💰 הזול ביותר: {money(cheapest, cur)} · שיא נמוך שנראה: {money(lowest, cur)}"
    if trend:
        footer += f"\n📊 {trend}"
    status.append(f"• {t['name']}: {money(cheapest, cur)} (שיא נמוך {money(lowest, cur)}) {trend}")

    new_state = {"updated": now_utc().isoformat(timespec="seconds"), "url": t["url"],
                 "lines": lines, "below_alerted": (old or {}).get("below_alerted")}

    if old is None:
        send(f"✅ מעקב הופעל: {t['name']}\nנמצאו {len(prices)} מחירים.\n{footer}", t["url"])
    else:
        since = old.get("updated", "?").replace("T", " ")[:16]
        events = analyze(old.get("lines", []), lines) if cards or parse_cards(old.get("lines", [])) else []
        want = set(t.get("alert_on", ["drop", "new", "gone"]))
        important = [e for e in events if e[0] in want]
        reasons = [e[1] for e in events if e[0] in want or important]  # rises ride along

        thr = t.get("alert_below")
        if thr and cheapest < thr and (new_state["below_alerted"] is None or cheapest < new_state["below_alerted"]):
            reasons.insert(0, f"🎯 מחיר מתחת לסף שלך ({money(thr, cur)}): {money(cheapest, cur)}")
            important.append(("thr", ""))
            new_state["below_alerted"] = cheapest
        elif thr and cheapest >= thr:
            new_state["below_alerted"] = None

        if not events and old.get("lines") != lines and not cards:
            # non-card page (calendar etc.): fall back to a text diff
            diff = [d for d in difflib.unified_diff(old.get("lines", []), lines, lineterm="", n=0)
                    if d[:1] in "+-" and d[:3] not in ("+++", "---")]
            if diff:
                reasons = ["שינוי בטקסט הדף:"] + diff[:12]
                important.append(("text", ""))

        if important:
            send(f"🔔 {t['name']}\n(מאז הבדיקה ב-{since} UTC)\n" + "\n".join(reasons[:15]) + f"\n\n{footer}", t["url"])
        else:
            quiet = f", {len(events)} שינויים לא משמעותיים" if events else ""
            print(f"  {t['name']}: nothing worth alerting{quiet}. cheapest {money(cheapest, cur)}"
                  f"{'' if prev_min is None else f' (prev {money(prev_min, cur)})'}")
    save_json(path, new_state)
    return True


def expand(targets):
    out = []
    for t in targets:
        if t.get("enabled", True) is False:
            print(f"skipping disabled target: {t['name']}")
            continue
        offsets = t.get("date_offsets") or [0]
        for off in offsets:
            u = dict(t)
            if off:
                m = re.search(r"\d{4}-\d{2}-\d{2}", t["url"])
                if not m:
                    continue
                d = datetime.strptime(m.group(), "%Y-%m-%d") + timedelta(days=off)
                u["url"] = t["url"].replace(m.group(), d.strftime("%Y-%m-%d"), 1)
                u["name"] = f"{t['name']} [{d.strftime('%d.%m')}]"
            u["slug"] = slug(u["name"])
            out.append(u)
    return out


def main():
    targets = expand(json.load(open(TARGETS, encoding="utf-8")))
    if ONLY:
        targets = [t for t in targets if ONLY in t["name"]]
    hist = load_json(os.path.join(DATA, "history.json"), {})
    health = load_json(os.path.join(DATA, "health.json"), {})
    meta = load_json(os.path.join(DATA, "meta.json"), {})
    status, ok = [], 0

    with sync_playwright() as p:
        # persistent context + headful Chrome: what Cloudflare treats as a normal visitor.
        # Do NOT add a custom UA, it breaks the fingerprint.
        kwargs = dict(user_data_dir=PROFILE, headless=os.environ.get("HEADLESS") == "1",
                      no_viewport=True, locale="he-IL", timezone_id="Asia/Jerusalem")
        try:
            ctx = p.chromium.launch_persistent_context(channel="chrome", **kwargs)
        except Exception as e:
            print(f"chrome channel unavailable ({str(e)[:60]}); using bundled chromium", file=sys.stderr)
            ctx = p.chromium.launch_persistent_context(**kwargs)
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        for t in targets:
            print(f"== {t['name']}")
            h = health.setdefault(t["slug"], {"fails": 0, "alerted": False})
            try:
                process(t, fetch(pg, t), hist, status)
                ok += 1
                if h["alerted"]:
                    send(f"✅ חזר לעבוד: {t['name']}", t["url"])
                h.update(fails=0, alerted=False)
            except Exception as e:
                h["fails"] += 1
                reason = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
                print(f"  FAILED ({h['fails']}x): {reason}", file=sys.stderr)
                if h["fails"] >= FAIL_ALERT_AFTER and not h["alerted"]:
                    send(f"⚠️ {t['name']} נכשל {h['fails']} פעמים ברצף.\nסיבה: {reason}\n"
                         "הנתונים לא מתעדכנים עד שזה יתוקן.", t["url"])
                    h["alerted"] = True
        ctx.close()

    # daily heartbeat (once per UTC day, after HEARTBEAT_HOUR_UTC)
    today = now_utc().strftime("%Y-%m-%d")
    if status and now_utc().hour >= HEARTBEAT_HOUR_UTC and meta.get("last_heartbeat") != today:
        send("💓 עדיין עובד. מצב נוכחי:\n" + "\n".join(status))
        meta["last_heartbeat"] = today

    save_json(os.path.join(DATA, "history.json"), hist)
    save_json(os.path.join(DATA, "health.json"), health)
    save_json(os.path.join(DATA, "meta.json"), meta)
    print(f"{ok}/{len(targets)} targets OK.")


if __name__ == "__main__":
    main()

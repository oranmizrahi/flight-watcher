"""One-off: dump El Al's search-form DOM + screenshots so the automation can be written."""
import sys
from patchright.sync_api import sync_playwright

def dump(pg, tag):
    pg.screenshot(path=f"probe-{tag}.png", full_page=True)
    rows = []
    for el in pg.query_selector_all("input, button, select, [role=combobox], [role=button], [role=option], [role=gridcell], a[href]"):
        try:
            rows.append(el.evaluate("""e=>[e.tagName,e.id,e.name,e.type||'',e.getAttribute('role')||'',
              e.getAttribute('placeholder')||'',e.getAttribute('aria-label')||'',
              e.getAttribute('data-testid')||'',(e.innerText||'').trim().slice(0,40).replace(/\\n/g,' '),
              e.getBoundingClientRect().width>0].join(' | ')"""))
        except Exception:
            pass
    open(f"probe-{tag}.txt", "w", encoding="utf-8").write("\n".join(rows))
    open(f"probe-{tag}.html", "w", encoding="utf-8").write(pg.content())
    print(tag, len(rows), "elements")

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(user_data_dir="/tmp/probe-prof", channel="chrome",
            headless=False, no_viewport=True, locale="he-IL", timezone_id="Asia/Jerusalem")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto("https://booking.elal.com/booking/flights?market=IL&lang=he", timeout=90000)
    pg.wait_for_timeout(10000)
    dump(pg, "0-initial")
    pg.locator("#passenger-counters-input").click(force=True)
    pg.wait_for_timeout(2500)
    dump(pg, "3-pax")
    ctx.close()

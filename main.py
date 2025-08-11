# Headless login with Playwright → call FPL API → suggest 3 best single-transfer upgrades → DM on Telegram
import os, asyncio, pytz, requests
from datetime import datetime
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# ----- env -----
EMAIL    = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID  = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]

# ----- consts -----
UA_STR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
BASE = "https://fantasy.premierleague.com/api"

# ----- utils -----
def now_ist():
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d %b %H:%M")

def ep(v):
    try:
        return float(v) if v not in (None, "", "0.0") else 0.0
    except Exception:
        return 0.0

def team_counts(player_ids, by_id):
    c = {}
    for pid in player_ids:
        t = by_id[pid]["team"]
        c[t] = c.get(t, 0) + 1
    return c

def tg_send(text: str):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=30,
    )
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

async def _accept_cookies(page):
    # best-effort cookie banners
    for sel in [
        'button:has-text("Accept all cookies")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        'text="Accept all cookies"',
    ]:
        try:
            await page.locator(sel).click(timeout=1500)
            print("DEBUG: clicked cookie button:", sel)
            break
        except Exception:
            pass

async def _find_and_fill(page, selectors, value, kind):
    """Try to fill value into selector on main page or any iframe."""
    # main frame
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            await loc.wait_for(state="visible", timeout=2500)
            await loc.fill(value)
            print(f"DEBUG: filled {kind} via main selector: {sel}")
            return True
        except Exception:
            pass
    # iframes
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        for sel in selectors:
            try:
                loc = fr.locator(sel).first
                await loc.wait_for(state="visible", timeout=2500)
                await loc.fill(value)
                print(f"DEBUG: filled {kind} via iframe {fr.url} selector: {sel}")
                return True
            except Exception:
                pass
    return False

async def _click_submit(page):
    buttons = [
        'button[type="submit"]',
        'button:has-text("Sign in")',
        'button:has-text("Log in")',
        'button:has-text("Sign In")',
        'button:has-text("Continue")',
    ]
    # main frame
    for sel in buttons:
        try:
            await page.locator(sel).first.click(timeout=2500)
            print(f"DEBUG: clicked submit on main via {sel}")
            return True
        except Exception:
            pass
    # iframes
    for fr in page.frames:
        if fr == page.main_frame:
            continue
        for sel in buttons:
            try:
                await fr.locator(sel).first.click(timeout=2500)
                print(f"DEBUG: clicked submit in iframe {fr.url} via {sel}")
                return True
            except Exception:
                pass
    return False

async def login_and_context(pw):
    # create browser/context
    browser = await pw.chromium.launch(headless=True)
    ctx = await browser.new_context(user_agent=UA_STR)
    page = await ctx.new_page()

    login_url = (
        "https://users.premierleague.com/accounts/login/"
        "?redirect_uri=https://fantasy.premierleague.com/&app=plfpl-web"
    )
    await page.goto(login_url, wait_until="domcontentloaded")
    await _accept_cookies(page)

    # Some trackers keep network busy forever → don't block on 'networkidle'
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except PWTimeout:
        pass  # harmless

    # Try multiple selectors (main + iframes)
    user_selectors = [
        'input[name="login"]',
        'input[name="email"]',
        'input[name="username"]',
        '#login',
        '#email',
        '#username',
        'input[type="email"]',
    ]
    pass_selectors = [
        'input[name="password"]',
        '#password',
        'input[type="password"]',
    ]

    filled_user = await _find_and_fill(page, user_selectors, EMAIL, "username")
    filled_pass = await _find_and_fill(page, pass_selectors, PASSWORD, "password")

    if not (filled_user and filled_pass):
        # some flows gate the form behind a button; try to reveal it
        print("DEBUG: inputs not visible initially — trying to reveal form…")
        await _click_submit(page)
        await page.wait_for_timeout(1500)
        filled_user = filled_user or await _find_and_fill(page, user_selectors, EMAIL, "username")
        filled_pass = filled_pass or await _find_and_fill(page, pass_selectors, PASSWORD, "password")

    if not (filled_user and filled_pass):
        print("DEBUG: frames present:", [f.url for f in page.frames])
        raise PWTimeout("Could not find login inputs on page or in iframes")

    await _click_submit(page)

    # Poll auth probe until 200 or timeout
    ok = False
    for _ in range(24):  # ~12s
        r = await ctx.request.get(f"{BASE}/me/")
        if r.status == 200:
            ok = True
            break
        await page.wait_for_timeout(500)

    if not ok:
        raise RuntimeError("Login failed: /api/me stayed non-200")

    return browser, ctx

# ----- main flow -----
async def main():
    async with async_playwright() as pw:
        browser, ctx = await login_and_context(pw)

        # Public players
        r = await ctx.request.get(f"{BASE}/bootstrap-static/")
        r.raise_for_status()
        boot = await r.json()
        elements = boot["elements"]
        by_id = {p["id"]: p for p in elements}

        # Your team (auth)
        r = await ctx.request.get(f"{BASE}/my-team/{TEAM_ID}/")
        r.raise_for_status()
        my_team = await r.json()

        picks = my_team["picks"]
        bank = int(my_team.get("transfers", {}).get("bank", 0))  # tenths of £m

        team_ids = [p["element"] for p in picks]
        pos_of = {pid: by_id[pid]["element_type"] for pid in team_ids}
        cost_of = {pid: by_id[pid]["now_cost"] for pid in team_ids}
        club_of = {pid: by_id[pid]["team"] for pid in team_ids}
        club_cnt = team_counts(team_ids, by_id)

        # candidate pool (active/doubt, not owned)
        pool_by_pos = {1: [], 2: [], 3: [], 4: []}
        for p in elements:
            if p["id"] in team_ids:
                continue
            if p["status"] not in ("a", "d"):
                continue
            pool_by_pos[p["element_type"]].append(p)

        # evaluate single-transfer upgrades under budget & 3-per-club
        suggestions = []
        for sell in team_ids:
            sell_pos = pos_of[sell]
            sell_cost = cost_of[sell]
            sell_club = club_of[sell]
            sell_xp = ep(by_id[sell]["ep_next"])

            counts = dict(club_cnt)
            counts[sell_club] -= 1
            budget = bank + sell_cost

            for cand in pool_by_pos[sell_pos]:
                buy_cost = cand["now_cost"]
                if buy_cost > budget:
                    continue
                buy_club = cand["team"]
                if counts.get(buy_club, 0) + 1 > 3:
                    continue
                delta = ep(cand["ep_next"]) - sell_xp
                if delta <= 0:
                    continue
                suggestions.append(
                    {
                        "out_name": by_id[sell]["web_name"],
                        "in_name": cand["web_name"],
                        "delta": round(delta, 2),
                        "out_cost": sell_cost / 10.0,
                        "in_cost": buy_cost / 10.0,
                    }
                )

        suggestions.sort(key=lambda x: x["delta"], reverse=True)
        top3, seen = [], set()
        for s in suggestions:
            key = (s["out_name"], s["in_name"])
            if key in seen:
                continue
            seen.add(key)
            top3.append(s)
            if len(top3) == 3:
                break

        if not top3:
            tg_send(f"({now_ist()}) No positive xP single-transfer upgrades found.")
        else:
            lines = [f"({now_ist()}) Top single-transfer upgrades by xP:"]
            for i, s in enumerate(top3, 1):
                lines.append(
                    f"{i}. {s['out_name']} → {s['in_name']} "
                    f"(ΔxP +{s['delta']}, £{s['out_cost']:.1f}m → £{s['in_cost']:.1f}m)"
                )
            tg_send("\n".join(lines))

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())

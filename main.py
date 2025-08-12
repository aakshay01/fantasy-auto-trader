# Self-hosted runner + persistent Chrome profile.
# First run may show a real Chrome window; you log in once. Cookies persist at ~/.fpl-profile.
# Then the script fetches your team and sends top 3 single-transfer upgrades (by ep_next) to Telegram.

import os
import asyncio
from pathlib import Path
from datetime import datetime

import pytz
import requests
from playwright.async_api import async_playwright

# ====== ENV (set as repo secrets) ======
EMAIL    = os.environ["FPL_EMAIL"]          # only for your reference
PASSWORD = os.environ["FPL_PASSWORD"]       # you'll type it in Chrome on first run if needed
TEAM_ID  = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]

# Optional: set to "true" to run headless after you’ve logged in once
HEADLESS = os.environ.get("FPL_HEADLESS", "").lower() in ("1", "true", "yes")

# ====== CONSTS ======
BASE = "https://fantasy.premierleague.com/api"
UA   = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PROFILE_DIR = Path.home() / ".fpl-profile"     # persistent browser profile lives here

def now_ist() -> str:
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d %b %H:%M")

def ep(v):
    try:
        return float(v) if v not in (None, "", "0.0") else 0.0
    except Exception:
        return 0.0

def team_counts(player_ids, by_id):
    counts = {}
    for pid in player_ids:
        t = by_id[pid]["team"]
        counts[t] = counts.get(t, 0) + 1
    return counts

def tg_send(text: str):
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text},
        timeout=30,
    )
    if r.status_code != 200:
        print("Telegram error:", r.status_code, r.text)

async def api_get_json(ctx, path: str):
    """Playwright request.get wrapper with proper status handling."""
    r = await ctx.request.get(f"{BASE}{path}")
    if r.status != 200:
        body = await r.text()
        raise RuntimeError(f"GET {path} -> {r.status}: {body}")
    return await r.json()

async def ensure_logged_in(ctx):
    """Return when /api/me returns 200. If not, open login and wait for you to sign in."""
    r = await ctx.request.get(f"{BASE}/me/")
    if r.status == 200:
        print("DEBUG: Already authenticated.")
        return

    # Not authenticated: open FPL site and let you log in.
    page = await ctx.new_page()
    print("\n=== ACTION NEEDED (first run only) ===")
    print("A Chrome window will open. Click 'Sign in' and log in to FPL.")
    print("If you see a 'holding' page or any challenge, complete it.")
    print("I’ll detect login automatically and continue.\n")

    await page.goto("https://fantasy.premierleague.com/", wait_until="domcontentloaded")

    # Give up to 5 minutes to complete login. Poll /api/me every 2 seconds.
    for _ in range(150):  # 150 * 2s = 300s
        r = await ctx.request.get(f"{BASE}/me/")
        if r.status == 200:
            print("DEBUG: Auth success detected.")
            await page.close()
            return
        await asyncio.sleep(2)

    await page.close()
    raise RuntimeError("Timed out waiting for manual login. Please run again and sign in in the Chrome window.")

async def run_bot():
    async with async_playwright() as pw:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

        # Persistent profile so cookies/token survive between runs
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",              # system Chrome looks most human
            headless=HEADLESS,             # first run: keep visible; later you can set FPL_HEADLESS=true
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        # Mild stealth tweaks
        await ctx.add_init_script("""Object.defineProperty(navigator,'webdriver',{get:()=>undefined});""")
        await ctx.add_init_script("""window.chrome = { runtime: {} };""")
        await ctx.add_init_script("""Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});""")
        await ctx.add_init_script("""Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});""")

        await ctx.set_extra_http_headers({"User-Agent": UA, "Referer": "https://fantasy.premierleague.com/"})

        # Ensure we are logged in (manual once)
        await ensure_logged_in(ctx)

        # ----- Public players -----
        boot = await api_get_json(ctx, "/bootstrap-static/")
        elements = boot["elements"]
        by_id = {p["id"]: p for p in elements}

        # ----- Your team (auth) -----
        my_team = await api_get_json(ctx, f"/my-team/{TEAM_ID}/")
        picks = my_team["picks"]
        bank  = int(my_team.get("transfers", {}).get("bank", 0))  # tenths of £m

        team_ids = [p["element"] for p in picks]
        pos_of   = {pid: by_id[pid]["element_type"] for pid in team_ids}
        cost_of  = {pid: by_id[pid]["now_cost"]      for pid in team_ids}
        club_of  = {pid: by_id[pid]["team"]          for pid in team_ids}
        club_cnt = team_counts(team_ids, by_id)

        # Candidate pool (active/doubt, not owned)
        pool_by_pos = {1: [], 2: [], 3: [], 4: []}
        for p in elements:
            if p["id"] in team_ids: continue
            if p["status"] not in ("a","d"): continue
            pool_by_pos[p["element_type"]].append(p)

        # Evaluate best single-transfer upgrades under budget & 3-per-club
        suggestions = []
        for sell in team_ids:
            sell_pos  = pos_of[sell]
            sell_cost = cost_of[sell]
            sell_club = club_of[sell]
            sell_xp   = ep(by_id[sell]["ep_next"])

            counts = dict(club_cnt); counts[sell_club] -= 1
            budget = bank + sell_cost

            for cand in pool_by_pos[sell_pos]:
                buy_cost = cand["now_cost"]
                if buy_cost > budget: continue
                buy_club = cand["team"]
                if counts.get(buy_club, 0) + 1 > 3: continue
                delta = ep(cand["ep_next"]) - sell_xp
                if delta <= 0: continue
                suggestions.append({
                    "out_name": by_id[sell]["web_name"],
                    "in_name":  cand["web_name"],
                    "delta":    round(delta, 2),
                    "out_cost": sell_cost/10.0,
                    "in_cost":  buy_cost/10.0
                })

        suggestions.sort(key=lambda x: x["delta"], reverse=True)
        seen, top3 = set(), []
        for s in suggestions:
            key = (s["out_name"], s["in_name"])
            if key in seen: continue
            seen.add(key); top3.append(s)
            if len(top3) == 3: break

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

        await ctx.close()  # closes browser too

def main():
    asyncio.run(run_bot())

if __name__ == "__main__":
    main()

# Minimal FPL helper with login: gets your current picks via /my-team/,
# computes top 3 single-transfer upgrades by ep_next (xP), and DMs you.

import os, math, pytz, requests, asyncio, aiohttp
from datetime import datetime
from telegram import Bot
from fpl import FPL

EMAIL = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

BASE = "https://fantasy.premierleague.com/api"

def jget(path):
    r = requests.get(f"{BASE}{path}", timeout=30)
    r.raise_for_status()
    return r.json()

def ep(v):
    try:
        return float(v) if v not in (None, "", "0.0") else 0.0
    except Exception:
        return 0.0

def now():
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d %b %H:%M")

def team_counts(player_ids, by_id):
    counts = {}
    for pid in player_ids:
        t = by_id[pid]["team"]
        counts[t] = counts.get(t, 0) + 1
    return counts

async def run():
    # 1) Public master data
    boot = jget("/bootstrap-static/")
    elements = boot["elements"]
    by_id = {p["id"]: p for p in elements}

    # 2) Login + get your /my-team/ (picks + bank in tenths of £m)
    async with aiohttp.ClientSession() as session:
        fpl = FPL(session)
        await fpl.login(EMAIL, PASSWORD)
        my_team = await fpl.get_my_team(TEAM_ID)

    # my_team.picks may be objects; normalize to dicts with 'element'
    picks = getattr(my_team, "picks", [])
    if picks and hasattr(picks[0], "element"):
        team_ids = [p.element for p in picks]
    else:
        team_ids = [p["element"] for p in picks]

    transfers = getattr(my_team, "transfers", {}) or {}
    bank = getattr(transfers, "bank", None)
    if bank is None and isinstance(transfers, dict):
        bank = transfers.get("bank", 0)
    bank = int(bank or 0)  # tenths of £m

    # helpers
    pos_of = {pid: by_id[pid]["element_type"] for pid in team_ids}
    cost_of = {pid: by_id[pid]["now_cost"] for pid in team_ids}
    club_of = {pid: by_id[pid]["team"] for pid in team_ids}
    team_club_counts = team_counts(team_ids, by_id)

    # 3) Candidate pool (only active/doubt players you don't own)
    pool_by_pos = {1: [], 2: [], 3: [], 4: []}
    for p in elements:
        if p["id"] in team_ids:
            continue
        if p["status"] not in ("a", "d"):
            continue
        pool_by_pos[p["element_type"]].append(p)

    # 4) Evaluate best single-transfer upgrades under budget & 3-per-club
    suggestions = []
    for sell in team_ids:
        sell_pos = pos_of[sell]
        sell_cost = cost_of[sell]
        sell_club = club_of[sell]
        sell_xp = ep(by_id[sell]["ep_next"])

        counts = dict(team_club_counts)
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
            suggestions.append({
                "out_id": sell,
                "in_id": cand["id"],
                "delta": round(delta, 2),
                "out_name": by_id[sell]["web_name"],
                "in_name": cand["web_name"],
                "out_cost": sell_cost/10.0,
                "in_cost": buy_cost/10.0,
            })

    suggestions.sort(key=lambda x: x["delta"], reverse=True)
    top3, seen = [], set()
    for s in suggestions:
        key = (s["out_id"], s["in_id"])
        if key in seen:
            continue
        seen.add(key)
        top3.append(s)
        if len(top3) == 3:
            break

    bot = Bot(TG_TOKEN)
    if not top3:
        bot.send_message(CHAT_ID, f"({now()}) No positive xP single-transfer upgrades found.")
        return

    lines = [f"({now()}) Top single-transfer upgrades by xP:"]
    for i, s in enumerate(top3, 1):
        lines.append(
            f"{i}. {s['out_name']} → {s['in_name']} "
            f"(ΔxP +{s['delta']}, £{s['out_cost']:.1f}m → £{s['in_cost']:.1f}m)"
        )
    bot.send_message(CHAT_ID, "\n".join(lines))

if __name__ == "__main__":
    asyncio.run(run())

# Logs into FPL using the official 'fpl' client, pulls your /my-team/ picks,
# computes top 3 single-transfer upgrades by ep_next (xP), and DMs you via Telegram API.

import os, asyncio, aiohttp, pytz
from datetime import datetime
from fpl import FPL

EMAIL = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

def now_ist():
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

async def send_telegram(session, text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    async with session.post(url, json=payload) as r:
        if r.status != 200:
            body = await r.text()
            print("Telegram send error:", r.status, body)

async def main():
    async with aiohttp.ClientSession(headers=UA) as session:
        # 1) FPL login via official client
        fpl = FPL(session)
        await fpl.login(EMAIL, PASSWORD)

        # 2) Public bootstrap for player data
        async with session.get("https://fantasy.premierleague.com/api/bootstrap-static/") as r:
            r.raise_for_status()
            boot = await r.json()
        elements = boot["elements"]
        by_id = {p["id"]: p for p in elements}

        # 3) Your team picks + bank
        my_team = await fpl.get_my_team(TEAM_ID)   # authenticated call
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

        pos_of   = {pid: by_id[pid]["element_type"] for pid in team_ids}
        cost_of  = {pid: by_id[pid]["now_cost"]      for pid in team_ids}
        club_of  = {pid: by_id[pid]["team"]          for pid in team_ids}
        club_cnt = team_counts(team_ids, by_id)

        # 4) Candidate pool (only active/doubt, not already owned)
        pool_by_pos = {1: [], 2: [], 3: [], 4: []}
        for p in elements:
            if p["id"] in team_ids: continue
            if p["status"] not in ("a", "d"): continue
            pool_by_pos[p["element_type"]].append(p)

        # 5) Evaluate single-transfer upgrades under budget & 3-per-club
        suggestions = []
        for sell in team_ids:
            sell_pos  = pos_of[sell]
            sell_cost = cost_of[sell]
            sell_club = club_of[sell]
            sell_xp   = ep(by_id[sell]["ep_next"])

            counts = dict(club_cnt)
            counts[sell_club] -= 1
            budget = bank + sell_cost

            for cand in pool_by_pos[sell_pos]:
                buy_cost = cand["now_cost"]
                if buy_cost > budget: continue
                buy_club = cand["team"]
                if counts.get(buy_club, 0) + 1 > 3: continue
                delta = ep(cand["ep_next"]) - sell_xp
                if delta <= 0: continue
                suggestions.append({
                    "out_id": sell, "in_id": cand["id"],
                    "out_name": by_id[sell]["web_name"],
                    "in_name": cand["web_name"],
                    "delta": round(delta, 2),
                    "out_cost": sell_cost/10.0, "in_cost": buy_cost/10.0
                })

        suggestions.sort(key=lambda x: x["delta"], reverse=True)
        top3, seen = [], set()
        for sug in suggestions:
            key = (sug["out_id"], sug["in_id"])
            if key in seen: continue
            seen.add(key); top3.append(sug)
            if len(top3) == 3: break

        if not top3:
            await send_telegram(session, f"({now_ist()}) No positive xP single-transfer upgrades found.")
            return

        lines = [f"({now_ist()}) Top single-transfer upgrades by xP:"]
        for i, sgg in enumerate(top3, 1):
            lines.append(
                f"{i}. {sgg['out_name']} → {sgg['in_name']} "
                f"(ΔxP +{sgg['delta']}, £{sgg['out_cost']:.1f}m → £{sgg['in_cost']:.1f}m)"
            )
        await send_telegram(session, "\n".join(lines))

if __name__ == "__main__":
    asyncio.run(main())

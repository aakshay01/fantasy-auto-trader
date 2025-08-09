# Minimal FPL helper: suggests 3 best single-transfer upgrades by xP (ep_next)
# No login required; uses public endpoints. Sends results to Telegram.

import os, requests, math, pytz
from datetime import datetime
from telegram import Bot

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

def build_maps(elements):
    by_id = {p["id"]: p for p in elements}
    by_teamcount = {}
    return by_id, by_teamcount

def team_counts(player_ids, by_id):
    counts = {}
    for pid in player_ids:
        t = by_id[pid]["team"]
        counts[t] = counts.get(t, 0) + 1
    return counts

def main():
    # 1) Pull master data & find next event
    boot = jget("/bootstrap-static/")
    elements = boot["elements"]
    events = boot["events"]
    by_id = {p["id"]: p for p in elements}

    next_event = next((e for e in events if e.get("is_next")), None)
    if not next_event:
        next_event = next((e for e in events if e.get("is_current")), events[0])
    gw = next_event["id"]

    # 2) Entry info (bank) + current picks (for next or current GW)
    entry = jget(f"/entry/{TEAM_ID}/")
    bank = int(entry.get("bank", 0))  # in tenths of £m

    def safe_picks(event_id):
        try:
            return jget(f"/entry/{TEAM_ID}/event/{event_id}/picks/")["picks"]
        except Exception:
            # fallback to current event if next not available yet
            cur = next((e for e in events if e.get("is_current")), events[-1])
            return jget(f"/entry/{TEAM_ID}/event/{cur['id']}/picks/")["picks"]

    picks = safe_picks(gw)
    team_ids = [p["element"] for p in picks]
    pos_of = {p["element"]: by_id[p["element"]]["element_type"] for p in picks}
    cost_of = {p["element"]: by_id[p["element"]]["now_cost"] for p in picks}
    club_of = {p["element"]: by_id[p["element"]]["team"] for p in picks}
    team_club_counts = team_counts(team_ids, by_id)

    # 3) Build candidate pool by position (only available players)
    pool_by_pos = {1: [], 2: [], 3: [], 4: []}
    for p in elements:
        if p["id"] in team_ids:  # already own
            continue
        if p["status"] not in ("a", "d"):  # active or doubt only
            continue
        pool_by_pos[p["element_type"]].append(p)

    # 4) Evaluate best single-transfer upgrades under budget & 3-per-club
    suggestions = []
    for sell in team_ids:
        sell_pos = pos_of[sell]
        sell_cost = cost_of[sell]
        sell_club = club_of[sell]
        sell_xp = ep(by_id[sell]["ep_next"])

        # Adjust counts if we sell first
        counts = dict(team_club_counts)
        counts[sell_club] -= 1

        budget = bank + sell_cost  # all in tenths of £m

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
                "out_name": f'{by_id[sell]["web_name"]}',
                "in_name": f'{cand["web_name"]}',
                "out_cost": sell_cost/10.0,
                "in_cost": buy_cost/10.0,
            })

    # Sort by delta desc, take top 3 unique by (out,in)
    suggestions.sort(key=lambda x: x["delta"], reverse=True)
    seen, top3 = set(), []
    for s in suggestions:
        key = (s["out_id"], s["in_id"])
        if key in seen:
            continue
        seen.add(key)
        top3.append(s)
        if len(top3) == 3:
            break

    # 5) Send Telegram DM
    bot = Bot(TG_TOKEN)
    if not top3:
        bot.send_message(CHAT_ID, f"({now()}) No positive xP single-transfer upgrades found this GW.")
        return

    lines = [f"({now()}) Top single-transfer upgrades by xP:"]
    for i, s in enumerate(top3, 1):
        lines.append(
            f"{i}. {s['out_name']} → {s['in_name']}  "
            f"(ΔxP +{s['delta']}, £{s['out_cost']:.1f}m → £{s['in_cost']:.1f}m)"
        )
    bot.send_message(CHAT_ID, "\n".join(lines))

if __name__ == "__main__":
    main()

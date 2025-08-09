# Minimal FPL helper with direct login via requests.Session.
# Logs in, fetches /api/my-team/{TEAM_ID}, suggests top 3 single-transfer
# upgrades by ep_next (xP), and DMs you on Telegram.

import os, pytz, requests
from datetime import datetime
from telegram import Bot

EMAIL = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0"}

def now_ist():
    return datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%d %b %H:%M")

def jget(sess, path):
    r = sess.get(f"{BASE}{path}", timeout=30, headers=UA)
    r.raise_for_status()
    return r.json()

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

def login_session(email, password):
    s = requests.Session()
    s.headers.update(UA)
    # Hit main site once to set cookies
    s.get("https://fantasy.premierleague.com/", timeout=30)
    # Classic login endpoint
    payload = {
        "login": email,
        "password": password,
        "app": "plfpl-web",
        "redirect_uri": "https://fantasy.premierleague.com/",
        "redirect": "false",
    }
    r = s.post(
        "https://users.premierleague.com/accounts/login/",
        data=payload,
        headers={
            **UA,
            "Referer": "https://fantasy.premierleague.com/",
            "Origin": "https://fantasy.premierleague.com",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
        allow_redirects=True,
    )
    # Login usually returns 200/204 and sets auth cookies; raise on hard failures
    if r.status_code not in (200, 204):
        r.raise_for_status()
    return s

def main():
    bot = Bot(TG_TOKEN)
    s = login_session(EMAIL, PASSWORD)

    boot = jget(s, "/bootstrap-static/")
    elements = boot["elements"]
    by_id = {p["id"]: p for p in elements}

    # Your squad + bank
    my_team = jget(s, f"/my-team/{TEAM_ID}/")
    picks = my_team["picks"]  # list of dicts with 'element'
    transfers = my_team.get("transfers", {})
    bank = int(transfers.get("bank", 0))  # tenths of £m

    team_ids = [p["element"] for p in picks]
    pos_of   = {pid: by_id[pid]["element_type"] for pid in team_ids}
    cost_of  = {pid: by_id[pid]["now_cost"] for pid in team_ids}
    club_of  = {pid: by_id[pid]["team"] for pid in team_ids}
    club_cnt = team_counts(team_ids, by_id)

    # Candidate pool: only active/doubt, not owned
    pool_by_pos = {1: [], 2: [], 3: [], 4: []}
    for p in elements:
        if p["id"] in team_ids: continue
        if p["status"] not in ("a", "d"): continue
        pool_by_pos[p["element_type"]].append(p)

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
                "out_id": sell,
                "in_id": cand["id"],
                "delta": round(delta, 2),
                "out_name": by_id[sell]["web_name"],
                "in_name": cand["web_name"],
                "out_cost": sell_cost/10.0,
                "in_cost": buy_cost/10.0,
            })

    suggestions.sort(key=lambda x: x["delta"], reverse=True)
    seen, top3 = set(), []
    for sgg in suggestions:
        key = (sgg["out_id"], sgg["in_id"])
        if key in seen: continue
        seen.add(key); top3.append(sgg)
        if len(top3) == 3: break

    if not top3:
        bot.send_message(CHAT_ID, f"({now_ist()}) No positive xP single-transfer upgrades found.")
        return

    lines = [f"({now_ist()}) Top single-transfer upgrades by xP:"]
    for i, sgg in enumerate(top3, 1):
        lines.append(
            f"{i}. {sgg['out_name']} → {sgg['in_name']} "
            f"(ΔxP +{sgg['delta']}, £{sgg['out_cost']:.1f}m → £{sgg['in_cost']:.1f}m)"
        )
    bot.send_message(CHAT_ID, "\n".join(lines))

if __name__ == "__main__":
    main()

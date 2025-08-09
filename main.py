# Login to FPL with CSRF, fetch /api/my-team/{TEAM_ID}, suggest 3 best single-transfer
# upgrades by ep_next (xP), send to Telegram. No heavy deps.

import os, pytz, requests
from datetime import datetime
from telegram import Bot

EMAIL = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

BASE = "https://fantasy.premierleague.com/api"
UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

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

def login_session(email, password):
    s = requests.Session()
    s.headers.update(UA)

    # 1) warm up main site (sets cookies like csrftoken)
    s.get("https://fantasy.premierleague.com/", timeout=30)

    # 2) hit the users login page to get a CSRF token
    login_page = s.get(
        "https://users.premierleague.com/accounts/login/",
        params={"redirect_uri": "https://fantasy.premierleague.com/", "app": "plfpl-web"},
        headers={**UA, "Referer": "https://fantasy.premierleague.com/"},
        timeout=30,
    )
    csrf = login_page.cookies.get("csrftoken") or s.cookies.get("csrftoken")

    payload = {
        "login": email,
        "password": password,
        "redirect_uri": "https://fantasy.premierleague.com/",
        "app": "plfpl-web",
        "remember": "true",
        "csrfmiddlewaretoken": csrf or "",
    }
    headers = {
        **UA,
        "Origin": "https://users.premierleague.com",
        "Referer": login_page.url,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    r = s.post(
        "https://users.premierleague.com/accounts/login/",
        data=payload,
        headers=headers,
        allow_redirects=True,
        timeout=30,
    )
    # some responses return 200 with JSON; others 204 after setting cookies
    if r.status_code not in (200, 204):
        r.raise_for_status()

    # sanity check: can we access /my-team/ now?
    t = s.get(f"{BASE}/my-team/{TEAM_ID}/", timeout=30, headers=UA)
    if t.status_code == 403:
        raise RuntimeError(
            "Forbidden on /my-team/. Check that FPL_TEAM_ID is YOUR own team id "
            "and credentials are correct. (Settings → Secrets → Actions)."
        )
    t.raise_for_status()
    return s

def main():
    bot = Bot(TG_TOKEN)
    s = login_session(EMAIL, PASSWORD)

    boot = s.get(f"{BASE}/bootstrap-static/", timeout=30, headers=UA).json()
    elements = boot["elements"]; by_id = {p["id"]: p for p in elements}

    my_team = s.get(f"{BASE}/my-team/{TEAM_ID}/", timeout=30, headers=UA).json()
    picks = my_team["picks"]
    bank  = int(my_team.get("transfers", {}).get("bank", 0))  # tenths of £m

    team_ids = [p["element"] for p in picks]
    pos_of   = {pid: by_id[pid]["element_type"] for pid in team_ids}
    cost_of  = {pid: by_id[pid]["now_cost"]      for pid in team_ids}
    club_of  = {pid: by_id[pid]["team"]          for pid in team_ids}
    club_cnt = team_counts(team_ids, by_id)

    # candidate pool
    pool_by_pos = {1: [], 2: [], 3: [], 4: []}
    for p in elements:
        if p["id"] in team_ids:     continue
        if p["status"] not in ("a","d"): continue
        pool_by_pos[p["element_type"]].append(p)

    suggestions = []
    for sell in team_ids:
        sell_pos  = pos_of[sell]; sell_cost = cost_of[sell]
        sell_club = club_of[sell]; sell_xp   = ep(by_id[sell]["ep_next"])

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
                "out_id": sell, "in_id": cand["id"],
                "out_name": by_id[sell]["web_name"],
                "in_name": cand["web_name"],
                "delta": round(delta, 2),
                "out_cost": sell_cost/10.0, "in_cost": buy_cost/10.0
            })

    suggestions.sort(key=lambda x: x["delta"], reverse=True)
    seen = set(); top3 = []
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

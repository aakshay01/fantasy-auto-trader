# Robust FPL login (CSRF + SSO), fetch /api/my-team/{TEAM_ID},
# suggest 3 best single-transfer upgrades by ep_next, DM via Telegram.
# Also prints small debug lines to Actions logs so we can diagnose 403s.

import os, requests, pytz
from datetime import datetime
from telegram import Bot

EMAIL = os.environ["FPL_EMAIL"]
PASSWORD = os.environ["FPL_PASSWORD"]
TEAM_ID = int(os.environ["FPL_TEAM_ID"])
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = int(os.environ["TELEGRAM_CHAT_ID"])

BASE = "https://fantasy.premierleague.com/api"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
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

    # 1) Warm up main site (sets base cookies)
    s.get("https://fantasy.premierleague.com/", timeout=30)

    # 2) Open accounts login page to get CSRF cookie
    params = {
        "redirect_uri": "https://fantasy.premierleague.com/",
        "app": "plfpl-web",
    }
    lp = s.get(
        "https://users.premierleague.com/accounts/login/",
        params=params,
        headers={**UA, "Referer": "https://fantasy.premierleague.com/"},
        timeout=30,
    )
    csrf = lp.cookies.get("csrftoken") or s.cookies.get("csrftoken") or ""

    payload = {
        "login": email,
        "password": password,
        "remember": "true",
        "redirect_uri": "https://fantasy.premierleague.com/",
        "app": "plfpl-web",
        "csrfmiddlewaretoken": csrf,
    }
    headers = {
        **UA,
        "Origin": "https://users.premierleague.com",
        "Referer": lp.url,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "X-CSRFToken": csrf,  # some deployments require this header too
    }

    r = s.post(
        "https://users.premierleague.com/accounts/login/",
        data=payload,
        headers=headers,
        allow_redirects=True,
        timeout=30,
    )

    # Debug: show key cookies after login
    cookie_names = sorted([c.name for c in s.cookies])
    print("DEBUG cookies after login:", cookie_names)
    print("DEBUG login status:", r.status_code)

    # 3) Hit a protected endpoint on fantasy domain to finalize SSO
    me = s.get(f"{BASE}/me/", timeout=30, headers=UA)
    print("DEBUG /api/me status:", me.status_code)

    if me.status_code in (401, 403):
        raise RuntimeError(
            "Login did not carry over to fantasy.premierleague.com (403 on /api/me). "
            "Common causes: wrong email/password, account has extra verification, "
            "or TEAM_ID isn’t your own entry."
        )
    me.raise_for_status()
    return s

def main():
    bot = Bot(TG_TOKEN)
    s = login_session(EMAIL, PASSWORD)

    boot = s.get(f"{BASE}/bootstrap-static/", timeout=30, headers=UA).json()
    elements = boot["elements"]; by_id = {p["id"]: p for p in elements}

    my_team_resp = s.get(f"{BASE}/my-team/{TEAM_ID}/", timeout=30, headers=UA)
    print("DEBUG /api/my-team status:", my_team_resp.status_code)
    my_team_resp.raise_for_status()
    my_team = my_team_resp.json()

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
        if p["id"] in team_ids: continue
        if p["status"] not in ("a", "d"): continue
        pool_by_pos[p["element_type"]].append(p)

    # evaluate upgrades
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
    top3, seen = [], set()
    for sug in suggestions:
        key = (sug["out_id"], sug["in_id"])
        if key in seen: continue
        seen.add(key); top3.append(sug)
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

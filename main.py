"""
main.py
--------
One-shot script that:
1) logs into Fantasy Premier League,
2) uses your optimiser to generate the 3 best transfer bundles,
3) sends them to you as buttons on Telegram.

You’ll wire this to a Render Cron Job so it runs every Friday.
"""

import os, asyncio, logging, pytz
from datetime import datetime

from fpl import FPL                    # pip package: python-fpl
import optimiser                       # <-- this file already exists in your repo
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode

# ---------- secrets come from Render / GitHub ----------
EMAIL = os.getenv("FPL_EMAIL")            # your FPL login
PASSWORD = os.getenv("FPL_PASSWORD")
TEAM_ID = int(os.getenv("FPL_TEAM_ID"))   # numeric ID from /entry/<id>/
TG_TOKEN = os.getenv("TELEGRAM_TOKEN")    # BotFather token
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# ---------- logging ----------
logging.basicConfig(level=logging.INFO)
IST = pytz.timezone("Asia/Kolkata")

# ---------- main workflow ----------
async def run():
    async with FPL() as fpl:
        await fpl.login(EMAIL, PASSWORD)
        logging.info("✔ Logged in to FPL")

        my_team = await fpl.get_my_team(TEAM_ID)
        logging.info("✔ Pulled current squad")

        # --- call your optimiser ---
        # Many forks expose a 'get_top_bundles' helper.  If yours differs,
        # open optimiser.py and swap the call below to whatever function returns
        # a list like:  [{"moves":[{"out":"Saka","in":"Diaz"}], "xp_delta":+5.2}, …]
        bundles = optimiser.get_top_bundles(my_team, top_n=3)
        logging.info("✔ Optimiser returned %d bundles", len(bundles))

        # --- craft Telegram message ---
        bot = Bot(TG_TOKEN)
        text_lines, buttons = [], []
        for i, b in enumerate(bundles, 1):
            move_str = ", ".join(
                f"*{m['out']}* → *{m['in']}*" for m in b["moves"]
            )
            text_lines.append(f"{i}. {move_str} _(ΔxP {b['xp_delta']:+.1f})_")
            buttons.append([InlineKeyboardButton(str(i), callback_data=str(i))])

        await bot.send_message(
            chat_id=CHAT_ID,
            text="💡 *Transfer options for this GW*:\n"
                 + "\n".join(text_lines),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN,
        )
        logging.info("✔ Sent Telegram menu")

if __name__ == "__main__":
    logging.info("⏱ Started at %s", datetime.now(IST).strftime("%c"))
    asyncio.run(run())

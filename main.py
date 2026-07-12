import os
from dotenv import load_dotenv

load_dotenv()

from logging_setup import setup_logging

# Must run before any stocksage module logs — attaches the rotating file
# handler (logs/stocksage.log) and the console handler.
logger = setup_logging()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing from .env")
if not CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID missing from .env")

from db.database import init_db, populate_from_config
from config import WATCHLIST
from agent.core import start_agent
from bot.telegram_bot import run_bot

if __name__ == "__main__":
    # Plain ASCII only in console-bound log messages -- Windows CMD's default
    # codepage (cp1252/cp850) cannot encode emoji.
    logger.info("[*] StockSage starting -- watchlist will be loaded from DB")

    # Init database and load watchlist
    init_db()
    populate_from_config(WATCHLIST)
    logger.info("[OK] Database ready")

    # Start background agent in separate thread
    start_agent(TOKEN, CHAT_ID)
    logger.info("[OK] Agent running in background")

    # Start Telegram bot (blocking - must be last)
    logger.info("[OK] Bot listening for commands...")
    run_bot(TOKEN)

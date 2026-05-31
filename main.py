import os
import threading
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TOKEN:
    raise ValueError("TELEGRAM_TOKEN missing from .env")
if not CHAT_ID:
    raise ValueError("TELEGRAM_CHAT_ID missing from .env")

print(f"[agent] StockSage starting — watchlist will be loaded from DB")

from db.database import init_db, populate_from_config
from config import WATCHLIST
from agent.core import start_agent
from bot.telegram_bot import run_bot

if __name__ == "__main__":
    print("🚀 StockSage starting...")

    # Init database and load watchlist
    init_db()
    populate_from_config(WATCHLIST)
    print("✅ Database ready")

    # Start background agent in separate thread
    start_agent(TOKEN, CHAT_ID)
    print("✅ Agent running in background")

    # Start Telegram bot (blocking - must be last)
    print("✅ Bot listening for commands...")
    run_bot(TOKEN)

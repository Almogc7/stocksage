import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
WEBAPP_DIR = BACKEND_DIR.parent

# Values already in the process environment win over the .env file.
load_dotenv(BACKEND_DIR / ".env", override=False)


def bot_db_path() -> Path:
    """Path to the bot DB (read-only). No default: fails loudly if unset."""
    raw = os.environ.get("STOCKSAGE_DB_PATH", "").strip()
    if not raw:
        raise RuntimeError(
            "STOCKSAGE_DB_PATH is not set. Define it in webapp/backend/.env "
            "(see .env.example)."
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError("STOCKSAGE_DB_PATH does not point to an existing file.")
    return path


def state_db_path() -> Path:
    """Webapp's own state DB (price cache, later layouts). Never the bot DB."""
    raw = os.environ.get("WEBAPP_STATE_DB_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return WEBAPP_DIR / "data" / "webapp_state.db"

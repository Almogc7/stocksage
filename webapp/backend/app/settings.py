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


def is_production() -> bool:
    return os.environ.get("WEBAPP_PRODUCTION", "").strip().lower() in ("1", "true", "yes")


def frontend_dist_path() -> Path:
    """Where the built frontend (npm run build's dist/) lives in
    production. Defaults outside the repo so a forgotten env var can't
    silently serve a stale dev build from inside the repo tree."""
    raw = os.environ.get("WEBAPP_FRONTEND_DIST_PATH", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(r"C:\stocksage-deploy\dist")


def repo_db_dir() -> Path:
    """The bot's own db/ directory (the live DB's location) — used only
    to REJECT a production STOCKSAGE_DB_PATH that points inside it."""
    return (WEBAPP_DIR.parent / "db").resolve()

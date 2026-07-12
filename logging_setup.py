"""
Central logging configuration for StockSage.

Call setup_logging() once at process startup (main.py does this). Modules
obtain loggers with logging.getLogger("stocksage.<area>") and never attach
handlers themselves.

Handlers are attached to the "stocksage" namespace logger only, NOT the root
logger, on purpose: third-party libraries (httpx in particular) log full
request URLs at INFO level, and Telegram request URLs contain the bot token.
Keeping library loggers off our file handler keeps credentials out of the log
file (see "Never print, log, or commit credentials" in CLAUDE.md).
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FILE = LOG_DIR / "stocksage.log"

_configured = False


def setup_logging(console_level: int = logging.INFO) -> logging.Logger:
    """Configure and return the "stocksage" namespace logger. Idempotent.

    - Rotating file log (logs/stocksage.log, UTF-8, 2 MB x 5 backups) at
      DEBUG level — the forensic trail, survives restarts.
    - Console echo at `console_level` (INFO by default). Log messages that
      reach the console should stay ASCII-only: Windows consoles on
      non-UTF-8 codepages can't encode emoji/Hebrew (logging swallows the
      resulting UnicodeEncodeError per-handler, but the line is lost).
    """
    global _configured
    logger = logging.getLogger("stocksage")
    if _configured:
        return logger

    LOG_DIR.mkdir(exist_ok=True)

    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # keep our records off the root logger

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s [%(threadName)s] %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    )

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    _configured = True
    return logger

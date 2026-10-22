"""Holiday-aware trading-day calendar helper for the setups/ stack
(docs/SETUP_SPEC.md §3).

Reuses services.watchlist_scheduler.is_us_market_day() directly rather than
copying its holiday algorithm -- confirmed safe: importing
services.watchlist_scheduler only pulls in db.database (defines DB_PATH and
functions, no connection opened at import time) and config (load_dotenv(),
a local file read) -- no network call, no scheduler start, no DB access, and
no yfinance import happens at import time.

Explicitly contrasts with data/market_data_validator.py's own
_most_recent_expected_trading_day(), which only steps back to the nearest
weekday and has no holiday awareness at all -- this spec requires the
holiday-aware version instead (§3).
"""
from datetime import date, timedelta

from services.watchlist_scheduler import is_us_market_day


def most_recent_us_trading_day(reference_date: date) -> date:
    """The latest date <= reference_date that is a US market trading day
    (weekday and not a known NYSE holiday, per is_us_market_day())."""
    d = reference_date
    while not is_us_market_day(d):
        d -= timedelta(days=1)
    return d

import asyncio
import io
import logging
import threading
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import schedule
from telegram import Bot

from analyzers.chart_generator import generate_chart_image
from analyzers.technical import full_analysis
from config import (
    ALERT_MIN_PRICE_CHANGE,
    ALERT_MIN_SCORE,
    ALERT_REQUIRE_GREEN_CANDLE,
    ALERT_VERDICTS,
    CHECK_INTERVAL_MINUTES,
    HEALTHCHECK_PING_URL,
    MARKET_OPEN_HOUR_IL,
    MARKET_OPEN_MIN_IL,
    SCAN_MIN_SCORE,
    SCAN_TOP_N,
)
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import get_active_watchlist, get_language, get_today_alerts, log_alert, was_alerted_today

logger = logging.getLogger("stocksage.agent")

_IL_TZ = ZoneInfo("Asia/Jerusalem")

_SEP = "━" * 13

# In-memory dedup guard: "SYMBOL_YYYY-MM-DD" → "HH:MM when alerted"
# Prevents duplicates within the same process even if the DB write hasn't
# become visible to a new connection yet (sqlite3 transaction isolation gap).
# Keyed on the UTC date so it agrees with the DB's once-per-UTC-day policy.
_alerted_this_session: dict[str, str] = {}


def _session_key(symbol: str) -> str:
    return f"{symbol.upper()}_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"


# ── Messaging ─────────────────────────────────────────────────────────────────

async def send_alert(bot: Bot, chat_id: str, message: str, symbol: str = "") -> None:
    label = f" [{symbol}]" if symbol else ""
    logger.info("[ALERT FIRE]%s Attempting Telegram send to chat_id=%s", label, chat_id)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        logger.info("[TELEGRAM OK]%s Message delivered successfully", label)
    except Exception as e:
        logger.error("[TELEGRAM ERROR]%s %s: %s", label, type(e).__name__, e)


async def send_alert_with_chart(
    bot: Bot, chat_id: str, caption: str, image_bytes: bytes, symbol: str = ""
) -> None:
    label = f" [{symbol}]" if symbol else ""
    logger.info("[ALERT FIRE]%s Attempting Telegram send_photo to chat_id=%s", label, chat_id)
    try:
        await bot.send_photo(chat_id=chat_id, photo=io.BytesIO(image_bytes), caption=caption)
        logger.info("[TELEGRAM OK]%s Photo delivered successfully", label)
    except Exception as e:
        logger.error(
            "[TELEGRAM ERROR]%s send_photo failed: %s: %s -- falling back to text",
            label, type(e).__name__, e,
        )
        try:
            await bot.send_message(chat_id=chat_id, text=caption)
            logger.info("[TELEGRAM OK]%s Fallback text delivered", label)
        except Exception as e2:
            logger.error(
                "[TELEGRAM ERROR]%s Fallback also failed: %s: %s",
                label, type(e2).__name__, e2,
            )


# ── Cooldown guard ────────────────────────────────────────────────────────────

def _in_cooldown(symbol: str) -> bool:
    """True if this symbol already alerted today (UTC day, D3) — skip it."""
    return was_alerted_today(symbol)


# ── Alert formatter ───────────────────────────────────────────────────────────

_ALERT_SEP = "━" * 19


def _fmt_buy_alert(symbol: str, change_pct: float, price: float, analysis: dict) -> str:
    score   = analysis["score"]
    verdict = analysis["verdict"]
    rsi     = analysis["rsi"]
    sign    = "+" if change_pct >= 0 else ""

    triggered    = analysis.get("triggered_signals", [])
    signal_names = [_SIGNAL_LABELS.get(s, s) for s in triggered]
    signals_str  = "  ".join(f"✅ {n}" for n in signal_names) if signal_names else "—"

    vol_tag = "✅" if "volume_spike" in triggered else "❌"

    return (
        f"\U0001f6a8 התראה — {symbol} {verdict} [{score}/100]\n"
        f"{_ALERT_SEP}\n"
        f"\U0001f4b0 מחיר: ${price:,.2f} ({sign}{change_pct:.1f}% היום)\n"
        f"\U0001f4ca ציון: {score}/100 | RSI: {rsi:.1f} ✅\n"
        f"\U0001f4c8 מעל SMA150 ✅ | נפח גבוה {vol_tag}\n"
        f"{signals_str}\n"
        f"\U0001f6d1 Stop: ${analysis['stop_loss']:,.2f} | \U0001f3af TP: ${analysis['take_profit']:,.2f}\n"
        f"⚖️ Risk/Reward: 1:2\n"
        f"{_ALERT_SEP}\n"
        f"\U0001f4a1 /analyze {symbol} לניתוח מלא"
    )


# ── Morning scan ──────────────────────────────────────────────────────────────

_SCAN_STRINGS: dict[str, dict[str, str]] = {
    "he": {
        "title":        "\U0001f305 סריקת בוקר — StockSage",
        "market_open":  "שוק פתוח",
        "full_analysis": "לניתוח מלא",
        "no_results":   "אין מניות עם ציון ≥ 50 כרגע.",
    },
    "en": {
        "title":        "\U0001f305 Morning Scan — StockSage",
        "market_open":  "Market Open",
        "full_analysis": "Full analysis",
        "no_results":   "No stocks found with score ≥ 50 right now.",
    },
}

_SIGNAL_LABELS: dict[str, str] = {
    "price_above_sma150":     "SMA trend",
    "sma150_above_sma200":    "SMA200 uptrend",
    "macd_bullish_crossover": "MACD cross",
    "rsi_healthy_range":      "RSI healthy",
    "rsi_acceptable_zone":    "RSI acceptable",
    "volume_spike":           "Volume spike",
    "stoch_rsi_bullish_cross": "Stoch RSI",
    "above_vwap":             "VWAP",
}

_MEDALS = ["\U0001f947", "\U0001f948", "\U0001f949"]  # 🥇 🥈 🥉
_SCAN_SEP = "━" * 19


def _fmt_morning_scan(results: list[dict], lang: str = "he") -> str:
    ss = _SCAN_STRINGS[lang]
    now = datetime.now(_IL_TZ)
    time_str = now.strftime("%H:%M")

    lines = [
        ss["title"],
        _SCAN_SEP,
        f"\U0001f551 {time_str} | {ss['market_open']}",
        "",
    ]

    for i, r in enumerate(results):
        medal = _MEDALS[i] if i < len(_MEDALS) else f"{i + 1}."
        signals = [_SIGNAL_LABELS.get(s, s) for s in r.get("triggered_signals", [])]
        signals_str = "  ".join(f"✅ {s}" for s in signals) if signals else "—"

        lines.append(f"{medal} {r['symbol']} — {r['verdict']} [{r['score']}/100]")
        lines.append(f"   {signals_str}")
        lines.append(f"   \U0001f6d1 Stop: ${r['stop_loss']:,.2f} | \U0001f3af TP: ${r['take_profit']:,.2f}")
        lines.append("")

    lines.append(_SCAN_SEP)
    lines.append(f"\U0001f4a1 /analyze SYMBOL {ss['full_analysis']}")
    return "\n".join(lines)


async def send_morning_scan(bot: Bot, chat_id: str, results: list[dict], lang: str = "he") -> None:
    if not results:
        message = f"\U0001f305 {_SCAN_STRINGS[lang]['no_results']}"
    else:
        message = _fmt_morning_scan(results, lang)
    logger.info("[SCAN] Sending morning scan (%d results) to chat_id=%s", len(results), chat_id)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        logger.info("[SCAN OK] Morning scan delivered")
    except Exception as e:
        logger.error("[SCAN ERROR] %s: %s", type(e).__name__, e)


_SCAN_SKIP_CATEGORIES = {"מדדים", "ETFs"}


async def run_morning_scan(bot: Bot, chat_id: str) -> None:
    logger.info("[SCAN] Starting morning scan across active watchlist...")
    wl = get_active_watchlist()  # only ACTIVE tier symbols

    # Build eligible symbol list from ACTIVE tier
    # (ETFs/indices are in ETF_INDEX_CONTEXT state and never appear here)
    eligible = [s for symbols in wl.values() for s in symbols]
    logger.info("[SCAN] %d eligible symbols in Active tier", len(eligible))

    results: list[dict] = []
    for symbol in eligible:
        try:
            df = get_historical(symbol, period="1y")
            if df is None:
                continue
            price_data = get_current_price(symbol)
            if not price_data:
                continue
            analysis = full_analysis(symbol, df, price_data["price"])
            if analysis["score"] >= SCAN_MIN_SCORE:
                results.append(analysis)
        except Exception as e:
            logger.warning("[SCAN SKIP] %s: %s: %s", symbol, type(e).__name__, e)
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:SCAN_TOP_N]
    lang = get_language(chat_id)
    logger.info(
        "[SCAN] %d qualifying symbols found, sending top %d (lang=%s)",
        len(results), len(top), lang,
    )
    await send_morning_scan(bot, chat_id, top, lang=lang)


# ── Check routine ─────────────────────────────────────────────────────────────

# triggered_signals entries that MUST be present for an alert to fire.
# These are full_analysis() outputs — the loop never re-derives RSI/MA/volume
# thresholds itself (single definition lives in analyzers/technical.py + config).
_REQUIRED_SIGNALS = ("rsi_healthy_range", "volume_spike")


async def check_alerts(bot: Bot, chat_id: str) -> None:
    """Single unified alert loop — all 7 gates must pass.

    Technical judgment (MA trend, RSI bands, volume) is consumed exclusively
    from full_analysis()'s score/verdict/triggered_signals; the loop adds only
    non-analysis gates: price movement, dedup/cooldown, and the completed-bar
    green-candle confirmation.
    """
    wl = get_active_watchlist()  # only ACTIVE tier symbols
    all_symbols = [s for symbols in wl.values() for s in symbols]
    if not all_symbols:
        return

    prices = get_multiple_prices(all_symbols)

    for symbol, price_data in prices.items():
        if not price_data:
            continue

        change_pct = price_data["change_pct"]

        # Gate 1: positive price movement ≥ ALERT_MIN_PRICE_CHANGE (BUY only)
        # DEBUG: fires for most symbols every tick — file log only, no console
        if change_pct < ALERT_MIN_PRICE_CHANGE:
            logger.debug(
                "[ALERT SKIP] %s -- price change %+.1f%% below threshold %s%%",
                symbol, change_pct, ALERT_MIN_PRICE_CHANGE,
            )
            continue

        # Gate 2: in-memory dedup — instant, no DB, catches same-cycle dupes
        key = _session_key(symbol)
        if key in _alerted_this_session:
            logger.info(
                "[DUPLICATE SKIP] %s -- already alerted today at %s",
                symbol, _alerted_this_session[key],
            )
            continue

        # Gate 3: DB once-per-day cooldown — persists across restarts
        if _in_cooldown(symbol):
            logger.info("[ALERT SKIP] %s -- already alerted today", symbol)
            continue

        # Gate 4: expensive compute — only reached if all cheap gates passed
        df = get_historical(symbol, period="1y")
        if df is None:
            continue

        analysis  = full_analysis(symbol, df, price_data["price"])
        score     = analysis["score"]
        verdict   = analysis["verdict"]
        rsi       = analysis["rsi"]
        triggered = analysis.get("triggered_signals", [])

        # Gate 7 candle selection — incomplete-bar bias avoidance.
        #
        # When the US market is open, yfinance includes today's in-progress
        # daily session as the last row of the daily df. Its "close" is the
        # most recent intraday price, NOT the final 4:00 PM close. Checking
        # "green candle" (close > open) against an unfinished session is
        # unreliable: a session that is currently green can close red.
        # This is *incomplete-bar bias* (not look-ahead bias — no future data
        # is used; the bar simply hasn't closed yet).
        #
        # When the market is closed, df.iloc[-1] IS the last completed session,
        # so we use it directly. When the market is open, we step back one row
        # to the previous confirmed close (df.iloc[-2]).
        if is_market_open() and len(df) >= 2:
            ref_candle = df.iloc[-2]
        else:
            ref_candle = df.iloc[-1]

        last_close = float(ref_candle["close"])
        last_open  = float(ref_candle["open"])

        # Gate 5: composite score + verdict (full_analysis has already vetoed
        # below-MA and out-of-band RSI to score 0 / NEUTRAL, so no separate
        # trend or RSI re-check belongs here)
        if score < ALERT_MIN_SCORE or verdict not in ALERT_VERDICTS:
            logger.info(
                "[ALERT SKIP] %s -- score=%s verdict=%s below threshold",
                symbol, score, verdict,
            )
            continue

        # Gate 6: required signals — healthy RSI band + volume spike, as
        # judged by full_analysis itself
        missing = [sig for sig in _REQUIRED_SIGNALS if sig not in triggered]
        if missing:
            logger.info(
                "[ALERT SKIP] %s -- missing required signals: %s",
                symbol, ", ".join(missing),
            )
            continue

        # Gate 7: last candle must be green (momentum confirmation)
        last_candle_green = last_close > last_open
        if ALERT_REQUIRE_GREEN_CANDLE and not last_candle_green:
            logger.info("[ALERT SKIP] %s -- last candle red, momentum fading", symbol)
            continue

        # All 7 gates passed → fire
        # ASCII-only log messages: the console handler on Hebrew-codepage
        # Windows can't encode chars like '→' (logging drops the line for
        # that handler instead of crashing, but the console trace is lost).
        logger.info(
            "[ALERT FIRE] %s score=%s RSI=%.1f change=%+.1f%% volume=spike -> SENDING",
            symbol, score, rsi, change_pct,
        )

        message = _fmt_buy_alert(symbol, change_pct, price_data["price"], analysis)

        # Attempt chart — df is already in memory from Gate 4, no extra fetch
        chart_bytes = generate_chart_image(symbol, df, analysis)
        if chart_bytes:
            caption = message if len(message) <= 1024 else message[:1021] + "..."
            await send_alert_with_chart(bot, chat_id, caption, chart_bytes, symbol=symbol)
        else:
            logger.warning("[CHART FAIL] %s -- sending text alert only", symbol)
            await send_alert(bot, chat_id, message, symbol=symbol)

        # Mark in-memory first so next symbol sees the guard before DB settles
        _alerted_this_session[key] = datetime.now().strftime("%H:%M")
        log_alert(symbol, "BUY_SIGNAL", message)
        logger.info("[agent] Alert sent: %s score=%s RSI=%.1f", symbol, score, rsi)


# ── Dead-man's switch ─────────────────────────────────────────────────────────

def _ping_healthcheck() -> None:
    """GET the configured monitoring URL (healthchecks.io style).

    Called at the end of every SUCCESSFUL run_checks() cycle — market open or
    closed — so the signal means "the scheduler daemon is alive and cycling",
    not "the market was scanned". If cycles start failing (the exception path
    in job() skips the ping) or the thread dies, pings stop and the monitoring
    service raises the alarm. No-op when HEALTHCHECK_PING_URL is unset.
    Failures here are logged and swallowed — monitoring must never break the
    alert loop.
    """
    if not HEALTHCHECK_PING_URL:
        return
    try:
        with urllib.request.urlopen(HEALTHCHECK_PING_URL, timeout=10):
            pass
        logger.debug("[healthcheck] ping OK")
    except Exception as e:
        logger.warning("[healthcheck] ping failed: %s: %s", type(e).__name__, e)


async def run_checks(bot: Bot, chat_id: str) -> None:
    market_open = is_market_open()
    logger.info("[agent] market_open=%s", market_open)
    if market_open:
        logger.info("[agent] running checks...")
        await check_alerts(bot, chat_id)
        logger.info("[agent] checks complete.")
    else:
        logger.info("[agent] Market closed (US 9:30-16:00 ET), skipping checks.")
    # Reached only if the cycle completed without an exception (a raise above
    # propagates to job(), which logs it and skips the ping).
    _ping_healthcheck()


# ── Scheduler ─────────────────────────────────────────────────────────────────

def start_agent(token: str, chat_id: str) -> threading.Thread:
    # Bot is created fresh inside each asyncio.run() so its httpx session
    # is not orphaned when the event loop closes between scheduler ticks.
    # Both jobs catch ALL exceptions themselves: a transient failure (Telegram
    # TimedOut, DNS outage, yfinance hiccup) must never propagate into the
    # scheduler loop and kill the daemon thread. Catching inside the job (as
    # opposed to only around schedule.run_pending()) also lets the `schedule`
    # lib mark the run as done, preserving the 15-minute cadence instead of
    # retrying a failing job every 60s.
    def job() -> None:
        async def _run() -> None:
            async with Bot(token) as bot:
                await run_checks(bot, chat_id)
        try:
            asyncio.run(_run())
        except Exception:
            logger.exception("[agent] check cycle failed -- will retry next tick")

    def morning_job() -> None:
        async def _run() -> None:
            async with Bot(token) as bot:
                await run_morning_scan(bot, chat_id)
        try:
            asyncio.run(_run())
        except Exception:
            logger.exception("[agent] morning scan failed")

    def _is_morning_scan_time(last_scan_date) -> bool:
        """True once per weekday when Israel time enters the 16:35 minute."""
        now = datetime.now(_IL_TZ)
        if now.weekday() >= 5:          # Sat=5, Sun=6
            return False
        if last_scan_date == now.date():  # already fired today
            return False
        return now.hour == MARKET_OPEN_HOUR_IL and now.minute >= MARKET_OPEN_MIN_IL

    def loop() -> None:
        last_scan_date = None
        job()  # run price/technical checks immediately on startup
        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(job)
        while True:
            # Backstop guard: job()/morning_job() already catch their own
            # exceptions; this keeps the daemon alive against anything
            # escaping `schedule` internals or the time check itself.
            # time.sleep stays OUTSIDE the try so a repeating failure can
            # never turn into a hot spin loop.
            try:
                schedule.run_pending()
                if _is_morning_scan_time(last_scan_date):
                    last_scan_date = datetime.now(_IL_TZ).date()
                    morning_job()
            except Exception:
                logger.exception("[agent] scheduler tick failed -- daemon thread continues")
            time.sleep(60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread

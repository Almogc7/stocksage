import asyncio
import io
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule
from telegram import Bot

from analyzers.chart_generator import generate_chart_image
from analyzers.technical import full_analysis
from config import (
    ALERT_COOLDOWN_HOURS,
    ALERT_MIN_PRICE_CHANGE,
    ALERT_MIN_SCORE,
    ALERT_REQUIRE_GREEN_CANDLE,
    ALERT_RSI_MAX,
    ALERT_RSI_MIN,
    ALERT_VERDICTS,
    CHECK_INTERVAL_MINUTES,
    MARKET_OPEN_HOUR_IL,
    MARKET_OPEN_MIN_IL,
    SCAN_MIN_SCORE,
    SCAN_TOP_N,
)
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import get_language, get_today_alerts, get_watchlist, log_alert, was_alerted_recently

_IL_TZ = ZoneInfo("Asia/Jerusalem")

_SEP = "━" * 13

# In-memory dedup guard: "SYMBOL_YYYY-MM-DD" → "HH:MM when alerted"
# Prevents duplicates within the same process even if the DB write hasn't
# become visible to a new connection yet (sqlite3 transaction isolation gap).
_alerted_this_session: dict[str, str] = {}


def _session_key(symbol: str) -> str:
    return f"{symbol.upper()}_{datetime.now().strftime('%Y-%m-%d')}"


# ── Messaging ─────────────────────────────────────────────────────────────────

async def send_alert(bot: Bot, chat_id: str, message: str, symbol: str = "") -> None:
    label = f" [{symbol}]" if symbol else ""
    print(f"[ALERT FIRE]{label} Attempting Telegram send to chat_id={chat_id}")
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        print(f"[TELEGRAM OK]{label} Message delivered successfully")
    except Exception as e:
        print(f"[TELEGRAM ERROR]{label} {type(e).__name__}: {e}")


async def send_alert_with_chart(
    bot: Bot, chat_id: str, caption: str, image_bytes: bytes, symbol: str = ""
) -> None:
    label = f" [{symbol}]" if symbol else ""
    print(f"[ALERT FIRE]{label} Attempting Telegram send_photo to chat_id={chat_id}")
    try:
        await bot.send_photo(chat_id=chat_id, photo=io.BytesIO(image_bytes), caption=caption)
        print(f"[TELEGRAM OK]{label} Photo delivered successfully")
    except Exception as e:
        print(f"[TELEGRAM ERROR]{label} send_photo failed: {type(e).__name__}: {e} — falling back to text")
        try:
            await bot.send_message(chat_id=chat_id, text=caption)
            print(f"[TELEGRAM OK]{label} Fallback text delivered")
        except Exception as e2:
            print(f"[TELEGRAM ERROR]{label} Fallback also failed: {type(e2).__name__}: {e2}")


# ── Cooldown guard ────────────────────────────────────────────────────────────

def _in_cooldown(symbol: str) -> bool:
    """True if this symbol was alerted within ALERT_COOLDOWN_HOURS — skip it."""
    return was_alerted_recently(symbol, hours=ALERT_COOLDOWN_HOURS)


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
        f"\U0001f4c8 מעל EMA150 ✅ | נפח גבוה {vol_tag}\n"
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
    "price_above_ema150":     "EMA trend",
    "ema150_above_ema200":    "EMA200 uptrend",
    "macd_bullish_crossover": "MACD cross",
    "rsi_healthy_range":      "RSI healthy",
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
    print(f"[SCAN] Sending morning scan ({len(results)} results) to chat_id={chat_id}")
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        print(f"[SCAN OK] Morning scan delivered")
    except Exception as e:
        print(f"[SCAN ERROR] {type(e).__name__}: {e}")


_SCAN_SKIP_CATEGORIES = {"מדדים", "ETFs"}


async def run_morning_scan(bot: Bot, chat_id: str) -> None:
    print(f"[SCAN] Starting morning scan across watchlist...")
    wl = get_watchlist()

    # Build eligible symbol list: skip index/ETF categories and ^ tickers
    eligible = [
        s
        for cat, symbols in wl.items()
        if cat not in _SCAN_SKIP_CATEGORIES
        for s in symbols
        if not s.startswith("^")
    ]
    print(f"[SCAN] {len(eligible)} eligible symbols after filtering indices/ETFs")

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
            print(f"[SCAN SKIP] {symbol}: {type(e).__name__}: {e}")
            continue

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:SCAN_TOP_N]
    lang = get_language(chat_id)
    print(f"[SCAN] {len(results)} qualifying symbols found, sending top {len(top)} (lang={lang})")
    await send_morning_scan(bot, chat_id, top, lang=lang)


# ── Check routine ─────────────────────────────────────────────────────────────

async def check_alerts(bot: Bot, chat_id: str) -> None:
    """Single unified alert loop — all 9 swing-trade conditions must pass."""
    wl = get_watchlist()
    all_symbols = [s for symbols in wl.values() for s in symbols]
    if not all_symbols:
        return

    prices = get_multiple_prices(all_symbols)

    for symbol, price_data in prices.items():
        if not price_data:
            continue

        change_pct = price_data["change_pct"]

        # Gate 1: positive price movement ≥ ALERT_MIN_PRICE_CHANGE (BUY only)
        if change_pct < ALERT_MIN_PRICE_CHANGE:
            print(f"[ALERT SKIP] {symbol} — price change {change_pct:+.1f}% below threshold {ALERT_MIN_PRICE_CHANGE}%")
            continue

        # Gate 2: in-memory dedup — instant, no DB, catches same-cycle dupes
        key = _session_key(symbol)
        if key in _alerted_this_session:
            print(f"[DUPLICATE SKIP] {symbol} — already alerted at {_alerted_this_session[key]}, cooldown {ALERT_COOLDOWN_HOURS}h")
            continue

        # Gate 3: DB cooldown — persists across restarts
        if _in_cooldown(symbol):
            print(f"[ALERT SKIP] {symbol} — in cooldown ({ALERT_COOLDOWN_HOURS}h)")
            continue

        # Gate 4: expensive compute — only reached if all cheap gates passed
        df = get_historical(symbol, period="1y")
        if df is None:
            continue

        analysis  = full_analysis(symbol, df, price_data["price"])
        score     = analysis["score"]
        verdict   = analysis["verdict"]
        rsi       = analysis["rsi"]
        above_ema = analysis["above_ema150"]
        triggered = analysis.get("triggered_signals", [])
        has_vol   = "volume_spike" in triggered

        # Candle data reused from df — no extra fetch
        last_close = float(df["close"].iloc[-1])
        last_open  = float(df["open"].iloc[-1])

        # Gate 5: price above EMA150
        if not above_ema:
            print(f"[ALERT SKIP] {symbol} — price below EMA150")
            continue

        # Gate 6: RSI in swing-trade zone (not oversold noise, not overbought)
        if rsi > ALERT_RSI_MAX:
            print(f"[ALERT SKIP] {symbol} — RSI {rsi:.1f} overbought (max {ALERT_RSI_MAX})")
            continue
        if rsi < ALERT_RSI_MIN:
            print(f"[ALERT SKIP] {symbol} — RSI {rsi:.1f} below minimum ({ALERT_RSI_MIN})")
            continue

        # Gate 7: volume spike confirms institutional participation
        if not has_vol:
            print(f"[ALERT SKIP] {symbol} — no volume spike")
            continue

        # Gate 8: composite score + verdict
        if score < ALERT_MIN_SCORE or verdict not in ALERT_VERDICTS:
            print(f"[ALERT SKIP] {symbol} — score={score} verdict={verdict} below threshold")
            continue

        # Gate 9: last candle must be green (momentum confirmation)
        last_candle_green = last_close > last_open
        if ALERT_REQUIRE_GREEN_CANDLE and not last_candle_green:
            print(f"[ALERT SKIP] {symbol} — last candle red, momentum fading")
            continue

        # All 9 gates passed → fire
        print(f"[ALERT FIRE] {symbol} score={score} RSI={rsi:.1f} change={change_pct:+.1f}% volume=spike → SENDING")

        message = _fmt_buy_alert(symbol, change_pct, price_data["price"], analysis)

        # Attempt chart — df is already in memory from Gate 4, no extra fetch
        chart_bytes = generate_chart_image(symbol, df, analysis)
        if chart_bytes:
            caption = message if len(message) <= 1024 else message[:1021] + "..."
            await send_alert_with_chart(bot, chat_id, caption, chart_bytes, symbol=symbol)
        else:
            print(f"[CHART FAIL] {symbol} — sending text alert only")
            await send_alert(bot, chat_id, message, symbol=symbol)

        # Mark in-memory first so next symbol sees the guard before DB settles
        _alerted_this_session[key] = datetime.now().strftime("%H:%M")
        log_alert(symbol, "BUY_SIGNAL", message)
        print(f"[agent] Alert sent: {symbol} score={score} RSI={rsi:.1f}")


async def run_checks(bot: Bot, chat_id: str) -> None:
    market_open = is_market_open()
    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — market_open={market_open}")
    if not market_open:
        print(f"[agent] Market closed (US 9:30–16:00 ET), skipping checks.")
        return

    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — running checks...")
    await check_alerts(bot, chat_id)
    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — checks complete.")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def start_agent(token: str, chat_id: str) -> threading.Thread:
    # Bot is created fresh inside each asyncio.run() so its httpx session
    # is not orphaned when the event loop closes between scheduler ticks.
    def job() -> None:
        async def _run() -> None:
            async with Bot(token) as bot:
                await run_checks(bot, chat_id)
        asyncio.run(_run())

    def morning_job() -> None:
        async def _run() -> None:
            async with Bot(token) as bot:
                await run_morning_scan(bot, chat_id)
        asyncio.run(_run())

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
            schedule.run_pending()
            if _is_morning_scan_time(last_scan_date):
                last_scan_date = datetime.now(_IL_TZ).date()
                morning_job()
            time.sleep(60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread

import asyncio
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import schedule
from telegram import Bot

from analyzers.technical import full_analysis
from config import (
    ALERT_COOLDOWN_HOURS,
    ALERT_MIN_SCORE,
    ALERT_THRESHOLD_PCT,
    ALERT_VERDICTS,
    CHECK_INTERVAL_MINUTES,
    INDEX_ALERT_THRESHOLD_PCT,
    MARKET_OPEN_HOUR_IL,
    MARKET_OPEN_MIN_IL,
    SCAN_MIN_SCORE,
    SCAN_TOP_N,
)
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import get_language, get_today_alerts, get_watchlist, log_alert, was_alerted_recently

_IL_TZ = ZoneInfo("Asia/Jerusalem")

_SEP = "━" * 13


# ── Messaging ─────────────────────────────────────────────────────────────────

async def send_alert(bot: Bot, chat_id: str, message: str, symbol: str = "") -> None:
    label = f" [{symbol}]" if symbol else ""
    print(f"[ALERT FIRE]{label} Attempting Telegram send to chat_id={chat_id}")
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        print(f"[TELEGRAM OK]{label} Message delivered successfully")
    except Exception as e:
        print(f"[TELEGRAM ERROR]{label} {type(e).__name__}: {e}")


# ── Cooldown guard ────────────────────────────────────────────────────────────

def _in_cooldown(symbol: str) -> bool:
    """True if this symbol was alerted within ALERT_COOLDOWN_HOURS — skip it."""
    return was_alerted_recently(symbol, hours=ALERT_COOLDOWN_HOURS)


# ── Alert formatters ──────────────────────────────────────────────────────────

def _fmt_price_alert(symbol: str, change_pct: float, price: float, analysis: dict) -> str:
    direction = "\U0001f4c8" if change_pct >= 0 else "\U0001f4c9"
    sign = "+" if change_pct >= 0 else ""
    ema_status = "פתוח ✅" if analysis["above_ema150"] else "סגור ❌"

    return (
        f"\U0001f6a8 התראת מחיר — {symbol}\n\n"
        f"{direction} שינוי: {sign}{change_pct:.1f}%\n"
        f"\U0001f4b0 מחיר: ${price:,.2f}\n\n"
        f"{_SEP}\n"
        f"\U0001f6a6 EMA150: {ema_status}\n"
        f"\U0001f3af ציון קנייה: {analysis['score']}/100\n"
        f"\U0001f4a1 המלצה: {analysis['verdict']}\n"
        f"{_SEP}\n"
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss']:,.2f}\n"
        f"\U0001f3af Take Profit: ${analysis['take_profit']:,.2f}\n\n"
        f"/analyze {symbol} לניתוח מלא"
    )


def _fmt_technical_alert(symbol: str, alert_type: str, analysis: dict) -> str:
    titles = {
        "STRONG_BUY":  f"\U0001f7e2 איתות קנייה חזק — {symbol}",
        "RSI_OVERSOLD": f"\U0001f4ca RSI מכירת יתר — {symbol}",
        "RSI_OVERBOUGHT": f"\U0001f4ca RSI קנייה יתר — {symbol}",
    }
    title = titles.get(alert_type, f"התראה טכנית — {symbol}")
    ema_status = "פתוח ✅" if analysis["above_ema150"] else "סגור ❌"

    return (
        f"{title}\n\n"
        f"\U0001f4b0 מחיר: ${analysis['current_price']:,.2f}\n"
        f"\U0001f4c8 RSI: {analysis['rsi']}\n"
        f"\U0001f6a6 EMA150: {ema_status}\n\n"
        f"{_SEP}\n"
        f"\U0001f3af ציון קנייה: {analysis['score']}/100\n"
        f"\U0001f4a1 המלצה: {analysis['verdict']}\n"
        f"{_SEP}\n"
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss']:,.2f}\n"
        f"\U0001f3af Take Profit: ${analysis['take_profit']:,.2f}\n\n"
        f"/analyze {symbol} לניתוח מלא"
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


# ── Check routines ────────────────────────────────────────────────────────────

async def check_price_alerts(bot: Bot, chat_id: str) -> None:
    wl = get_watchlist()
    all_symbols = [s for symbols in wl.values() for s in symbols]
    if not all_symbols:
        return

    prices = get_multiple_prices(all_symbols)

    for symbol, price_data in prices.items():
        if not price_data:
            continue

        change_pct = price_data["change_pct"]
        if abs(change_pct) < ALERT_THRESHOLD_PCT:
            continue

        # Cooldown: skip if this symbol was alerted in the last N hours
        if _in_cooldown(symbol):
            print(f"[ALERT SKIP] {symbol} in cooldown ({ALERT_COOLDOWN_HOURS}h)")
            continue

        df = get_historical(symbol, period="1y")
        if df is None:
            continue

        analysis = full_analysis(symbol, df, price_data["price"])
        score   = analysis["score"]
        verdict = analysis["verdict"]
        print(f"[ALERT CHECK] {symbol} price_change={change_pct:+.1f}% score={score} verdict={verdict}")

        # Price move is necessary but NOT sufficient — must also pass score+verdict gate
        if score < ALERT_MIN_SCORE or verdict not in ALERT_VERDICTS:
            print(f"[ALERT SKIP] {symbol} score={score} verdict={verdict} — does not meet BUY threshold")
            continue

        alert_type = "PRICE_UP" if change_pct >= 0 else "PRICE_DOWN"
        message = _fmt_price_alert(symbol, change_pct, price_data["price"], analysis)
        await send_alert(bot, chat_id, message, symbol=symbol)
        log_alert(symbol, alert_type, message)
        print(f"[agent] Price alert sent: {symbol} {change_pct:+.1f}% score={score}")


async def check_technical_alerts(bot: Bot, chat_id: str) -> None:
    wl = get_watchlist()
    all_symbols = [s for symbols in wl.values() for s in symbols]

    for symbol in all_symbols:
        df = get_historical(symbol, period="1y")
        if df is None:
            continue

        price_data = get_current_price(symbol)
        if not price_data:
            continue

        analysis = full_analysis(symbol, df, price_data["price"])
        score   = analysis["score"]
        verdict = analysis["verdict"]
        rsi     = analysis["rsi"]
        print(f"[ALERT CHECK] {symbol} score={score} verdict={verdict} rsi={rsi} min={ALERT_MIN_SCORE}")

        # Both conditions must pass the score+verdict gate before any alert fires
        qualifies = score >= ALERT_MIN_SCORE and verdict in ALERT_VERDICTS

        if not qualifies:
            continue

        # Cooldown: one alert per symbol per N hours regardless of alert_type
        if _in_cooldown(symbol):
            print(f"[ALERT SKIP] {symbol} in cooldown ({ALERT_COOLDOWN_HOURS}h)")
            continue

        alert_type = "STRONG_BUY"
        # Prefer the more specific RSI_OVERSOLD label when RSI is extreme
        if rsi < 25:
            alert_type = "RSI_OVERSOLD"

        message = _fmt_technical_alert(symbol, alert_type, analysis)
        await send_alert(bot, chat_id, message, symbol=symbol)
        log_alert(symbol, alert_type, message)
        print(f"[agent] Technical alert sent: {symbol} — {alert_type} score={score}")


async def run_checks(bot: Bot, chat_id: str) -> None:
    market_open = is_market_open()
    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — market_open={market_open}")
    if not market_open:
        print(f"[agent] Market closed (US 9:30–16:00 ET), skipping checks.")
        return

    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — running checks...")
    await check_price_alerts(bot, chat_id)
    await check_technical_alerts(bot, chat_id)
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

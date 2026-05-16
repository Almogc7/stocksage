import asyncio
import threading
import time
from datetime import datetime

import schedule
from telegram import Bot

from analyzers.technical import full_analysis
from config import ALERT_THRESHOLD_PCT, CHECK_INTERVAL_MINUTES, INDEX_ALERT_THRESHOLD_PCT
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import get_today_alerts, get_watchlist, log_alert

_SEP = "━" * 13


# ── Messaging ─────────────────────────────────────────────────────────────────

async def send_alert(bot: Bot, chat_id: str, message: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f"[agent] Failed to send alert: {e}")


# ── Duplicate guard ───────────────────────────────────────────────────────────

def _already_alerted_today(symbol: str, alert_type: str) -> bool:
    today_alerts = get_today_alerts()
    return any(
        a["symbol"] == symbol.upper() and a["alert_type"] == alert_type
        for a in today_alerts
    )


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
        f"\U0001f3af ציון קנייה: {analysis['buy_score']}/100\n"
        f"\U0001f4a1 המלצה: {analysis['recommendation']}\n"
        f"{_SEP}\n"
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss_15x']:,.2f}\n"
        f"\U0001f3af Take Profit: ${analysis['take_profit_2x']:,.2f}\n\n"
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
        f"\U0001f3af ציון קנייה: {analysis['buy_score']}/100\n"
        f"\U0001f4a1 המלצה: {analysis['recommendation']}\n"
        f"{_SEP}\n"
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss_15x']:,.2f}\n"
        f"\U0001f3af Take Profit: ${analysis['take_profit_2x']:,.2f}\n\n"
        f"/analyze {symbol} לניתוח מלא"
    )


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

        alert_type = "PRICE_UP" if change_pct >= 0 else "PRICE_DOWN"
        if _already_alerted_today(symbol, alert_type):
            continue

        df = get_historical(symbol, period="1y")
        if df is None:
            continue

        analysis = full_analysis(symbol, df, price_data["price"])
        message = _fmt_price_alert(symbol, change_pct, price_data["price"], analysis)

        await send_alert(bot, chat_id, message)
        log_alert(symbol, alert_type, message)
        print(f"[agent] Price alert sent: {symbol} {change_pct:+.1f}%")


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

        checks = []
        if analysis["recommendation"] == "STRONG_BUY":
            checks.append("STRONG_BUY")
        if analysis["rsi"] < 25:
            checks.append("RSI_OVERSOLD")
        if analysis["rsi"] > 75:
            checks.append("RSI_OVERBOUGHT")

        for alert_type in checks:
            if _already_alerted_today(symbol, alert_type):
                continue
            message = _fmt_technical_alert(symbol, alert_type, analysis)
            await send_alert(bot, chat_id, message)
            log_alert(symbol, alert_type, message)
            print(f"[agent] Technical alert sent: {symbol} — {alert_type}")


async def run_checks(bot: Bot, chat_id: str) -> None:
    if not is_market_open():
        print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — market closed, skipping checks.")
        return

    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — running checks...")
    await check_price_alerts(bot, chat_id)
    await check_technical_alerts(bot, chat_id)
    print(f"[agent] {datetime.now().strftime('%H:%M:%S')} — checks complete.")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def start_agent(token: str, chat_id: str) -> threading.Thread:
    bot = Bot(token)

    def job() -> None:
        asyncio.run(run_checks(bot, chat_id))

    def loop() -> None:
        job()  # run immediately on startup
        schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(job)
        while True:
            schedule.run_pending()
            time.sleep(60)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread

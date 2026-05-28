from datetime import datetime

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from analyzers.technical import full_analysis
from config import ALERT_THRESHOLD_PCT, CATEGORIES, WATCHLIST
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import (
    add_to_watchlist,
    get_today_alerts,
    get_trade_summary,
    get_trades,
    get_watchlist,
    init_db,
    log_alert,
    log_trade,
    remove_from_watchlist,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sep() -> str:
    return "━" * 17


def _ema_line(analysis: dict) -> str:
    status = "✅ פתוח" if analysis["above_ema150"] else "❌ סגור"
    return f"\U0001f6a6 EMA150: ${analysis['ema150']:,.2f} {status}"


def _rsi_label(signal: str) -> str:
    return {"oversold": "מכירת יתר", "overbought": "קנייה יתר"}.get(signal, "ניטרלי")


def _macd_label(crossover: str) -> str:
    return {"bullish": "חצייה שורית \U0001f4c8", "bearish": "חצייה דובית \U0001f4c9"}.get(crossover, "אין crossover")


def _bb_label(position: str) -> str:
    return {
        "above_upper": "מעל הבנד העליון",
        "near_upper":  "קרוב לבנד העליון",
        "middle":      "אמצע הבנד",
        "near_lower":  "קרוב לבנד התחתון",
        "below_lower": "מתחת לבנד התחתון",
    }.get(position, position)


def _rec_emoji(verdict: str) -> str:
    return {
        "STRONG BUY": "\U0001f7e2",
        "BUY":        "\U0001f7e1",
        "WEAK BUY":   "\U0001f7e0",
        "WATCH":      "⚪",
        "AVOID":      "\U0001f534",
    }.get(verdict, "⚪")


def _fmt_analysis(analysis: dict) -> str:
    sym = analysis["symbol"]
    price = analysis["current_price"]
    verdict = analysis["verdict"]

    triggered = analysis.get("triggered_signals", [])
    signals_str = "  •  ".join(triggered) if triggered else "—"

    lines = [
        f"\U0001f4ca ניתוח {sym} — ${price:,.2f}",
        "",
        _sep(),
        _ema_line(analysis),
        f"\U0001f4c8 RSI: {analysis['rsi']} — {_rsi_label(analysis['signal'])}",
        f"\U0001f4c9 MACD: {_macd_label(analysis['crossover'])}",
        f"\U0001f4ca Bollinger: {_bb_label(analysis['position'])}",
        _sep(),
        f"\U0001f3af ציון: {analysis['score']}/100",
        f"\U0001f4a1 המלצה: {_rec_emoji(verdict)} {verdict}",
        f"✅ איתותים: {signals_str}",
        _sep(),
        f"ATR: ${analysis['atr']:,.2f} ({analysis['atr_pct']}%)",
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss']:,.2f}",
        f"\U0001f3af Take Profit: ${analysis['take_profit']:,.2f}",
    ]
    return "\n".join(lines)


async def _send(update: Update, text: str) -> None:
    await update.message.reply_text(text)


async def _err_no_data(update: Update, symbol: str) -> None:
    await _send(update, f"❌ לא נמצאו נתונים עבור {symbol}")


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "\U0001f4ca ברוך הבא ל-StockSage!\n\n"
        "הנה הפקודות הזמינות:\n\n"
        "/analyze <SYMBOL> — ניתוח טכני מלא\n"
        "/add <SYMBOL> <CATEGORY> — הוספה ל-Watchlist\n"
        "/remove <SYMBOL> — הסרה מה-Watchlist\n"
        "/watchlist — הצגת ה-Watchlist עם מחירים\n"
        "/trade <BUY|SELL> <SYMBOL> <QUANTITY> <PRICE> [NOTE] — רישום עסקה\n"
        "/trades [SYMBOL] — היסטוריית עסקאות\n"
        "/summary <SYMBOL> — סיכום עסקאות וP&L\n"
        "/alerts — התראות היום\n"
        "/status — סטטוס השוק"
    )
    await _send(update, text)


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send(update, "שימוש: /analyze <SYMBOL>\nדוגמה: /analyze NVDA")
        return

    symbol = context.args[0].upper()
    await _send(update, f"⏳ מושך נתונים עבור {symbol}...")

    price_data = get_current_price(symbol)
    if not price_data:
        await _err_no_data(update, symbol)
        return

    df = get_historical(symbol, period="1y")
    if df is None:
        await _err_no_data(update, symbol)
        return

    analysis = full_analysis(symbol, df, price_data["price"])
    await _send(update, _fmt_analysis(analysis))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await _send(update, "שימוש: /add <SYMBOL> <CATEGORY>\nדוגמה: /add NVDA Tech")
        return

    symbol = context.args[0].upper()
    category = " ".join(context.args[1:])

    if category not in CATEGORIES:
        cats = "\n".join(f"  • {c}" for c in CATEGORIES)
        await _send(update, f"❌ קטגוריה לא קיימת: {category}\n\nקטגוריות זמינות:\n{cats}")
        return

    add_to_watchlist(symbol, category)
    await _send(update, f"✅ {symbol} נוסף לקטגוריה {category}")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send(update, "שימוש: /remove <SYMBOL>\nדוגמה: /remove NVDA")
        return

    symbol = context.args[0].upper()
    remove_from_watchlist(symbol)
    await _send(update, f"\U0001f5d1 {symbol} הוסר מה-Watchlist")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wl = get_watchlist()
    if not wl:
        await _send(update, "\U0001f4cb ה-Watchlist ריק.")
        return

    all_symbols = [s for symbols in wl.values() for s in symbols]
    await _send(update, "⏳ מושך מחירים...")
    prices = get_multiple_prices(all_symbols)

    lines = ["\U0001f4cb Watchlist\n"]
    for category, symbols in wl.items():
        lines.append(f"\U0001f4c2 {category}")
        for sym in symbols:
            p = prices.get(sym)
            if p:
                sign = "+" if p["change_pct"] >= 0 else ""
                lines.append(f"  {sym:<6} ${p['price']:>9,.2f}  {sign}{p['change_pct']}%")
            else:
                lines.append(f"  {sym:<6}  N/A")
        lines.append("")

    await _send(update, "\n".join(lines).strip())


async def cmd_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    usage = "שימוש: /trade <BUY|SELL> <SYMBOL> <QUANTITY> <PRICE> [NOTE]\nדוגמה: /trade BUY NVDA 10 225.32"
    if len(context.args) < 4:
        await _send(update, usage)
        return

    action = context.args[0].upper()
    if action not in ("BUY", "SELL"):
        await _send(update, f"❌ פעולה לא חוקית: {action}\n{usage}")
        return

    symbol = context.args[1].upper()
    try:
        quantity = float(context.args[2])
        price = float(context.args[3])
    except ValueError:
        await _send(update, f"❌ כמות ומחיר חייבים להיות מספרים\n{usage}")
        return

    note = " ".join(context.args[4:]) if len(context.args) > 4 else ""
    log_trade(action, symbol, quantity, price, note)

    total = quantity * price

    # Best-effort ATR for stop/target levels
    stop_str = "N/A"
    target_str = "N/A"
    df = get_historical(symbol, period="6mo")
    if df is not None:
        from analyzers.technical import calc_atr
        atr_data = calc_atr(df)
        stop_str = f"${price - atr_data['atr'] * 1.5:,.2f}"
        target_str = f"${price + atr_data['atr'] * 2:,.2f}"

    action_heb = "קנייה" if action == "BUY" else "מכירה"
    text = (
        f"✅ עסקה נרשמה!\n\n"
        f"פעולה: {action_heb}\n"
        f"מניה: {symbol}\n"
        f"כמות: {quantity:g} @ ${price:,.2f}\n"
        f'סה״כ: ${total:,.2f}\n'
        f"\U0001f6d1 Stop Loss מומלץ: {stop_str}\n"
        f"\U0001f3af Take Profit מומלץ: {target_str}"
    )
    await _send(update, text)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    symbol = context.args[0].upper() if context.args else None
    trades = get_trades(symbol)

    if not trades:
        label = f" עבור {symbol}" if symbol else ""
        await _send(update, f"\U0001f4cb אין עסקאות{label}.")
        return

    header = f"\U0001f4cb {'עסקאות ' + symbol if symbol else 'כל העסקאות'} (אחרונות 10)\n\n"
    rows = []
    for t in trades[:10]:
        dt = str(t["traded_at"])[:10]
        rows.append(
            f"{t['action']:<4}  {t['symbol']:<6}  {t['quantity']:>6g} @ ${t['price']:>8,.2f}  {dt}"
        )

    await _send(update, header + "\n".join(rows))


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send(update, "שימוש: /summary <SYMBOL>\nדוגמה: /summary NVDA")
        return

    symbol = context.args[0].upper()
    s = get_trade_summary(symbol)

    if s["total_quantity"] == 0 and s["avg_buy_price"] == 0:
        await _send(update, f"❌ אין עסקאות עבור {symbol}")
        return

    pnl = s["realized_pnl"]
    pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
    sign = "+" if pnl >= 0 else ""

    text = (
        f"\U0001f4ca סיכום עסקאות — {symbol}\n\n"
        f"מחיר קנייה ממוצע: ${s['avg_buy_price']:,.4f}\n"
        f"כמות נוכחית: {s['total_quantity']:g}\n"
        f"{pnl_emoji} רווח/הפסד ממומש: {sign}${pnl:,.2f}"
    )
    await _send(update, text)


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    alerts = get_today_alerts()

    if not alerts:
        await _send(update, "\U0001f514 אין התראות להיום.")
        return

    lines = ["\U0001f514 התראות היום\n"]
    for a in alerts:
        time_str = str(a["triggered_at"])[11:16]
        lines.append(f"[{time_str}] {a['symbol']} — {a['alert_type']}\n{a['message']}\n")

    await _send(update, "\n".join(lines).strip())


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = f"✅ StockSage bot is alive — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"[TELEGRAM OK] /test command confirmed delivery to chat_id={update.effective_chat.id}")
    await _send(update, text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    open_ = is_market_open()
    status_line = "\U0001f7e2 השוק פתוח" if open_ else "\U0001f534 השוק סגור"

    wl = get_watchlist()
    total = sum(len(v) for v in wl.values())

    vix_line = ""
    vix_data = get_current_price("^VIX")
    if vix_data:
        vix_line = f"\U0001f321 VIX: {vix_data['price']} ({'+' if vix_data['change_pct'] >= 0 else ''}{vix_data['change_pct']}%)\n"

    text = (
        f"\U0001f4f1 סטטוס StockSage\n\n"
        f"{status_line}\n"
        f"{vix_line}"
        f"\U0001f4cb Watchlist: {total} מניות ב-{len(wl)} קטגוריות"
    )
    await _send(update, text)


# ── Bot runner ────────────────────────────────────────────────────────────────

def run_bot(token: str) -> None:
    init_db(WATCHLIST)

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("test",      cmd_test))
    app.add_handler(CommandHandler("analyze",   cmd_analyze))
    app.add_handler(CommandHandler("add",       cmd_add))
    app.add_handler(CommandHandler("remove",    cmd_remove))
    app.add_handler(CommandHandler("watchlist", cmd_watchlist))
    app.add_handler(CommandHandler("trade",     cmd_trade))
    app.add_handler(CommandHandler("trades",    cmd_trades))
    app.add_handler(CommandHandler("summary",   cmd_summary))
    app.add_handler(CommandHandler("alerts",    cmd_alerts))
    app.add_handler(CommandHandler("status",    cmd_status))

    app.run_polling()

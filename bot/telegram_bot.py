from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from agent.core import run_morning_scan
from analyzers.technical import full_analysis
from config import (
    ALERT_COOLDOWN_HOURS, ALERT_THRESHOLD_PCT, AUTHORIZED_CHAT_IDS,
    CATEGORIES, DEFAULT_LANGUAGE, WATCHLIST,
)
from data.fetcher import get_current_price, get_historical, get_multiple_prices, is_market_open
from db.database import (
    add_to_watchlist,
    get_language,
    get_muted_symbols,
    get_today_alerts,
    get_trade_summary,
    get_trades,
    get_watchlist,
    init_db,
    log_alert,
    log_trade,
    remove_from_watchlist,
    set_language,
)

# ── Authorization ────────────────────────────────────────────────────────────

async def _check_auth(update: Update) -> bool:
    """
    Return True if the request comes from an authorized chat ID.

    Authorization is configured via AUTHORIZED_CHAT_IDS in config.py, which
    reads the AUTHORIZED_CHAT_IDS env var (comma-separated IDs) and falls back
    to TELEGRAM_CHAT_ID for single-user personal bots.

    If no authorized IDs are configured at all, every request is rejected
    (fail-secure). Unauthorized attempts are logged to stdout without
    exposing message content or the bot token.
    """
    if not AUTHORIZED_CHAT_IDS:
        print(f"[AUTH BLOCK] No AUTHORIZED_CHAT_IDS configured — rejecting all commands")
        return False
    chat_id = str(update.effective_chat.id)
    if chat_id not in AUTHORIZED_CHAT_IDS:
        print(f"[AUTH BLOCK] Unauthorized command from chat_id={chat_id}")
        return False
    return True


# ── Strings ───────────────────────────────────────────────────────────────────

STRINGS: dict[str, dict[str, str]] = {
    "he": {
        # start
        "start_welcome":         "\U0001f4ca ברוך הבא ל-StockSage!\n\nהקלד /help לרשימת כל הפקודות.\nהקלד /language en לאנגלית.",
        # scan
        "scan_title":            "\U0001f305 סריקת בוקר — StockSage",
        "scan_running":          "\U0001f305 מריץ סריקת מניות — זה יכול לקחת כמה דקות...",
        "market_open":           "שוק פתוח",
        "stop":                  "Stop",
        "take_profit":           "TP",
        "full_analysis":         "לניתוח מלא",
        "no_results":            "לא נמצאו מניות עם ציון מעל 50",
        # watchlist
        "watchlist_title":       "\U0001f4cb Watchlist",
        "watchlist_empty":       "\U0001f4cb ה-Watchlist ריק.",
        "fetching_prices":       "⏳ מושך מחירים...",
        "added":                 "נוסף לקטגוריה",
        "removed":               "הוסר מה-Watchlist",
        # analysis
        "not_found":             "לא נמצאו נתונים עבור",
        "fetching_data":         "⏳ מושך נתונים עבור",
        "analysis_title":        "ניתוח",
        "buy_score":             "ציון",
        "recommendation":        "המלצה",
        "signals":               "איתותים",
        "ema_above":             "✅ פתוח",
        "ema_below":             "❌ סגור",
        "rsi_oversold":          "מכירת יתר",
        "rsi_overbought":        "קנייה יתר",
        "rsi_neutral":           "ניטרלי",
        "macd_bullish":          "חצייה שורית \U0001f4c8",
        "macd_bearish":          "חצייה דובית \U0001f4c9",
        "macd_none":             "אין crossover",
        "bb_above_upper":        "מעל הבנד העליון",
        "bb_near_upper":         "קרוב לבנד העליון",
        "bb_middle":             "אמצע הבנד",
        "bb_near_lower":         "קרוב לבנד התחתון",
        "bb_below_lower":        "מתחת לבנד התחתון",
        # usage / errors
        "analyze_usage":         "שימוש: /analyze <SYMBOL>\nדוגמה: /analyze NVDA",
        "add_usage":             "שימוש: /add <SYMBOL> <CATEGORY>\nדוגמה: /add NVDA Tech",
        "remove_usage":          "שימוש: /remove <SYMBOL>\nדוגמה: /remove NVDA",
        "trade_usage":           "שימוש: /trade <BUY|SELL> <SYMBOL> <כמות> <מחיר> [הערה]\nדוגמה: /trade BUY NVDA 10 225.32",
        "summary_usage":         "שימוש: /summary <SYMBOL>\nדוגמה: /summary NVDA",
        "unknown_category":      "קטגוריה לא קיימת",
        "available_categories":  "קטגוריות זמינות",
        "invalid_number":        "כמות ומחיר חייבים להיות מספרים",
        # trades
        "trade_recorded":        "עסקה נרשמה",
        "action_label":          "פעולה",
        "symbol_label":          "מניה",
        "qty_label":             "כמות",
        "total_label":           'סה״כ',
        "action_buy":            "קנייה",
        "action_sell":           "מכירה",
        "recommended_stop":      "Stop Loss מומלץ",
        "recommended_tp":        "Take Profit מומלץ",
        "no_trades":             "אין עסקאות",
        "all_trades":            "כל העסקאות",
        "trades_label":          "עסקאות",
        "last_10":               "אחרונות 10",
        "avg_buy_price":         "מחיר קנייה ממוצע",
        "current_qty":           "כמות נוכחית",
        "realized_pnl":          "רווח/הפסד ממומש",
        "no_trades_for":         "אין עסקאות עבור",
        "summary_title":         "סיכום עסקאות",
        # alerts
        "no_alerts":             "\U0001f514 אין התראות להיום.",
        "alerts_title":          "\U0001f514 התראות היום",
        # status
        "market_open_hours":     "\U0001f7e2 השוק פתוח — נסגר ב-23:00",
        "market_closed_weekend": "\U0001f534 השוק סגור — סוף שבוע",
        "market_closed_hours":   "\U0001f534 השוק סגור — נפתח ב-16:30",
        "alerts_active_today":   "התראות פעילות היום",
        "muted_label":           "ממתינות (cooldown {hours}h)",
        "none_label":            "אין",
        "market_status_open":    "\U0001f7e2 השוק פתוח",
        "market_status_closed":  "\U0001f534 השוק סגור",
        "status_title":          "\U0001f4f1 סטטוס StockSage",
        "watchlist_stocks":      "מניות ב",
        "watchlist_cats":        "קטגוריות",
        # test
        "test_msg":              "StockSage פועל תקין",
        # help
        "help_title":            "\U0001f916 StockSage — פקודות זמינות",
        "help_analysis_sec":     "\U0001f4ca ניתוח",
        "help_watchlist_sec":    "\U0001f4cb Watchlist",
        "help_trades_sec":       "\U0001f4bc עסקאות",
        "help_tools_sec":        "⚙️ כלים",
        "help_analyze":          "ניתוח טכני מלא",
        "help_scan":             "סריקת מניות חמות עכשיו",
        "help_watchlist_cmd":    "הצג את כל המניות",
        "help_add":              "הוסף מניה",
        "help_remove":           "הסר מניה",
        "help_trade":            "תעד עסקה",
        "help_trades_cmd":       "היסטוריית עסקאות",
        "help_summary":          "סיכום פוזיציה",
        "help_alerts":           "התראות היום",
        "help_status":           "סטטוס שוק",
        "help_test":             "בדיקת חיבור Bot",
        "help_help":             "הצג תפריט זה",
        "help_language":         "שנה שפה",
    },
    "en": {
        # start
        "start_welcome":         "\U0001f4ca Welcome to StockSage!\n\nType /help to see all available commands.\nType /language he to switch to Hebrew.",
        # scan
        "scan_title":            "\U0001f305 Morning Scan — StockSage",
        "scan_running":          "\U0001f305 Running stock scan — this may take a few minutes...",
        "market_open":           "Market Open",
        "stop":                  "Stop",
        "take_profit":           "Target",
        "full_analysis":         "Full analysis",
        "no_results":            "No stocks found with score above 50",
        # watchlist
        "watchlist_title":       "\U0001f4cb Watchlist",
        "watchlist_empty":       "\U0001f4cb Watchlist is empty.",
        "fetching_prices":       "⏳ Fetching prices...",
        "added":                 "Added to category",
        "removed":               "Removed from Watchlist",
        # analysis
        "not_found":             "No data found for",
        "fetching_data":         "⏳ Fetching data for",
        "analysis_title":        "Analysis",
        "buy_score":             "Score",
        "recommendation":        "Recommendation",
        "signals":               "Signals",
        "ema_above":             "✅ Above",
        "ema_below":             "❌ Below",
        "rsi_oversold":          "Oversold",
        "rsi_overbought":        "Overbought",
        "rsi_neutral":           "Neutral",
        "macd_bullish":          "Bullish crossover \U0001f4c8",
        "macd_bearish":          "Bearish crossover \U0001f4c9",
        "macd_none":             "No crossover",
        "bb_above_upper":        "Above upper band",
        "bb_near_upper":         "Near upper band",
        "bb_middle":             "Middle band",
        "bb_near_lower":         "Near lower band",
        "bb_below_lower":        "Below lower band",
        # usage / errors
        "analyze_usage":         "Usage: /analyze <SYMBOL>\nExample: /analyze NVDA",
        "add_usage":             "Usage: /add <SYMBOL> <CATEGORY>\nExample: /add NVDA Tech",
        "remove_usage":          "Usage: /remove <SYMBOL>\nExample: /remove NVDA",
        "trade_usage":           "Usage: /trade <BUY|SELL> <SYMBOL> <QTY> <PRICE> [NOTE]\nExample: /trade BUY NVDA 10 225.32",
        "summary_usage":         "Usage: /summary <SYMBOL>\nExample: /summary NVDA",
        "unknown_category":      "Unknown category",
        "available_categories":  "Available categories",
        "invalid_number":        "Quantity and price must be numbers",
        # trades
        "trade_recorded":        "Trade recorded",
        "action_label":          "Action",
        "symbol_label":          "Symbol",
        "qty_label":             "Qty",
        "total_label":           "Total",
        "action_buy":            "Buy",
        "action_sell":           "Sell",
        "recommended_stop":      "Recommended Stop Loss",
        "recommended_tp":        "Recommended Take Profit",
        "no_trades":             "No trades",
        "all_trades":            "All trades",
        "trades_label":          "Trades for",
        "last_10":               "last 10",
        "avg_buy_price":         "Average buy price",
        "current_qty":           "Current quantity",
        "realized_pnl":          "Realized P&L",
        "no_trades_for":         "No trades for",
        "summary_title":         "Trade Summary",
        # alerts
        "no_alerts":             "\U0001f514 No alerts today.",
        "alerts_title":          "\U0001f514 Today's Alerts",
        # status
        "market_open_hours":     "\U0001f7e2 Market open — closes at 16:00 EST",
        "market_closed_weekend": "\U0001f534 Market closed — weekend",
        "market_closed_hours":   "\U0001f534 Market closed — opens at 09:30 EST",
        "alerts_active_today":   "Active alerts today",
        "muted_label":           "Muted (last {hours}h)",
        "none_label":            "None",
        "market_status_open":    "\U0001f7e2 Market is open",
        "market_status_closed":  "\U0001f534 Market is closed",
        "status_title":          "\U0001f4f1 StockSage Status",
        "watchlist_stocks":      "stocks in",
        "watchlist_cats":        "categories",
        # test
        "test_msg":              "StockSage is running",
        # help
        "help_title":            "\U0001f916 StockSage — Available Commands",
        "help_analysis_sec":     "\U0001f4ca Analysis",
        "help_watchlist_sec":    "\U0001f4cb Watchlist",
        "help_trades_sec":       "\U0001f4bc Trades",
        "help_tools_sec":        "⚙️ Tools",
        "help_analyze":          "Full technical analysis",
        "help_scan":             "Hot stocks scan now",
        "help_watchlist_cmd":    "Show all stocks",
        "help_add":              "Add a stock",
        "help_remove":           "Remove a stock",
        "help_trade":            "Record a trade",
        "help_trades_cmd":       "Trade history",
        "help_summary":          "Position summary",
        "help_alerts":           "Today's alerts",
        "help_status":           "Market status",
        "help_test":             "Connection test",
        "help_help":             "Show this menu",
        "help_language":         "Change language",
    },
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sep() -> str:
    return "━" * 17


def _rec_emoji(verdict: str) -> str:
    return {
        "STRONG BUY": "\U0001f7e2",
        "BUY":        "\U0001f7e1",
        "WEAK BUY":   "\U0001f7e0",
        "WATCH":      "⚪",
        "AVOID":      "\U0001f534",
    }.get(verdict, "⚪")


def _fmt_analysis(analysis: dict, lang: str = "he") -> str:
    s = STRINGS[lang]
    sym = analysis["symbol"]
    price = analysis["current_price"]
    verdict = analysis["verdict"]

    triggered = analysis.get("triggered_signals", [])
    signals_str = "  •  ".join(triggered) if triggered else "—"

    ema_status = s["ema_above"] if analysis["above_ema150"] else s["ema_below"]
    rsi_label  = {"oversold": s["rsi_oversold"], "overbought": s["rsi_overbought"]}.get(
        analysis["signal"], s["rsi_neutral"]
    )
    macd_label = {"bullish": s["macd_bullish"], "bearish": s["macd_bearish"]}.get(
        analysis["crossover"], s["macd_none"]
    )
    bb_label = {
        "above_upper": s["bb_above_upper"],
        "near_upper":  s["bb_near_upper"],
        "middle":      s["bb_middle"],
        "near_lower":  s["bb_near_lower"],
        "below_lower": s["bb_below_lower"],
    }.get(analysis["position"], analysis["position"])

    lines = [
        f"\U0001f4ca {s['analysis_title']} {sym} — ${price:,.2f}",
        "",
        _sep(),
        f"\U0001f6a6 EMA150: ${analysis['ema150']:,.2f} {ema_status}",
        f"\U0001f4c8 RSI: {analysis['rsi']} — {rsi_label}",
        f"\U0001f4c9 MACD: {macd_label}",
        f"\U0001f4ca Bollinger: {bb_label}",
        _sep(),
        f"\U0001f3af {s['buy_score']}: {analysis['score']}/100",
        f"\U0001f4a1 {s['recommendation']}: {_rec_emoji(verdict)} {verdict}",
        f"✅ {s['signals']}: {signals_str}",
        _sep(),
        f"ATR: ${analysis['atr']:,.2f} ({analysis['atr_pct']}%)",
        f"\U0001f6d1 Stop Loss: ${analysis['stop_loss']:,.2f}",
        f"\U0001f3af Take Profit: ${analysis['take_profit']:,.2f}",
    ]
    return "\n".join(lines)


async def _send(update: Update, text: str) -> None:
    await update.message.reply_text(text)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    await _send(update, STRINGS[lang]["start_welcome"])


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    chat_id = str(update.effective_chat.id)
    args = context.args

    if not args or args[0].lower() not in ("he", "en"):
        lang = get_language(chat_id)
        current = "עברית \U0001f1ee\U0001f1f1" if lang == "he" else "English \U0001f1ec\U0001f1e7"
        await _send(update,
            f"\U0001f310 שפה נוכחית: {current}\n\n"
            f"לשינוי שפה:\n"
            f"/language he — עברית\n"
            f"/language en — English"
        )
        return

    new_lang = args[0].lower()
    set_language(chat_id, new_lang)

    if new_lang == "he":
        await _send(update,
            "✅ השפה שונתה לעברית \U0001f1ee\U0001f1f1\n"
            "Language changed to Hebrew")
    else:
        await _send(update,
            "✅ Language changed to English \U0001f1ec\U0001f1e7\n"
            "השפה שונתה לאנגלית")


async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    if not context.args:
        await _send(update, s["analyze_usage"])
        return

    symbol = context.args[0].upper()
    await _send(update, f"{s['fetching_data']} {symbol}...")

    price_data = get_current_price(symbol)
    if not price_data:
        await _send(update, f"❌ {s['not_found']} {symbol}")
        return

    df = get_historical(symbol, period="1y")
    if df is None:
        await _send(update, f"❌ {s['not_found']} {symbol}")
        return

    analysis = full_analysis(symbol, df, price_data["price"])
    await _send(update, _fmt_analysis(analysis, lang))


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    if len(context.args) < 2:
        await _send(update, s["add_usage"])
        return

    symbol = context.args[0].upper()
    category = " ".join(context.args[1:])

    if category not in CATEGORIES:
        cats = "\n".join(f"  • {c}" for c in CATEGORIES)
        await _send(update, f"❌ {s['unknown_category']}: {category}\n\n{s['available_categories']}:\n{cats}")
        return

    add_to_watchlist(symbol, category)
    await _send(update, f"✅ {symbol} {s['added']} {category}")


async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    if not context.args:
        await _send(update, s["remove_usage"])
        return

    symbol = context.args[0].upper()
    remove_from_watchlist(symbol)
    await _send(update, f"\U0001f5d1 {symbol} {s['removed']}")


async def cmd_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    wl = get_watchlist()
    if not wl:
        await _send(update, s["watchlist_empty"])
        return

    all_symbols = [sym for symbols in wl.values() for sym in symbols]
    await _send(update, s["fetching_prices"])
    prices = get_multiple_prices(all_symbols)

    lines = [f"{s['watchlist_title']}\n"]
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
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    if len(context.args) < 4:
        await _send(update, s["trade_usage"])
        return

    action = context.args[0].upper()
    if action not in ("BUY", "SELL"):
        await _send(update, f"❌ {action}\n{s['trade_usage']}")
        return

    symbol = context.args[1].upper()
    try:
        quantity = float(context.args[2])
        price    = float(context.args[3])
    except ValueError:
        await _send(update, f"❌ {s['invalid_number']}\n{s['trade_usage']}")
        return

    note = " ".join(context.args[4:]) if len(context.args) > 4 else ""
    log_trade(action, symbol, quantity, price, note)

    total = quantity * price
    stop_str   = "N/A"
    target_str = "N/A"
    df = get_historical(symbol, period="6mo")
    if df is not None:
        from analyzers.technical import calc_atr
        atr_data   = calc_atr(df)
        stop_str   = f"${price - atr_data['atr'] * 1.5:,.2f}"
        target_str = f"${price + atr_data['atr'] * 2:,.2f}"

    action_label = s["action_buy"] if action == "BUY" else s["action_sell"]
    text = (
        f"✅ {s['trade_recorded']}!\n\n"
        f"{s['action_label']}: {action_label}\n"
        f"{s['symbol_label']}: {symbol}\n"
        f"{s['qty_label']}: {quantity:g} @ ${price:,.2f}\n"
        f"{s['total_label']}: ${total:,.2f}\n"
        f"\U0001f6d1 {s['recommended_stop']}: {stop_str}\n"
        f"\U0001f3af {s['recommended_tp']}: {target_str}"
    )
    await _send(update, text)


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    symbol = context.args[0].upper() if context.args else None
    trades = get_trades(symbol)

    if not trades:
        label = f" {symbol}" if symbol else ""
        await _send(update, f"\U0001f4cb {s['no_trades']}{label}.")
        return

    title = (
        f"{s['trades_label']} {symbol}"
        if symbol else
        s["all_trades"]
    )
    header = f"\U0001f4cb {title} ({s['last_10']})\n\n"
    rows = []
    for t in trades[:10]:
        dt = str(t["traded_at"])[:10]
        rows.append(
            f"{t['action']:<4}  {t['symbol']:<6}  {t['quantity']:>6g} @ ${t['price']:>8,.2f}  {dt}"
        )

    await _send(update, header + "\n".join(rows))


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    if not context.args:
        await _send(update, s["summary_usage"])
        return

    symbol = context.args[0].upper()
    data   = get_trade_summary(symbol)

    if data["total_quantity"] == 0 and data["avg_buy_price"] == 0:
        await _send(update, f"❌ {s['no_trades_for']} {symbol}")
        return

    pnl       = data["realized_pnl"]
    pnl_emoji = "\U0001f7e2" if pnl >= 0 else "\U0001f534"
    sign      = "+" if pnl >= 0 else ""

    text = (
        f"\U0001f4ca {s['summary_title']} — {symbol}\n\n"
        f"{s['avg_buy_price']}: ${data['avg_buy_price']:,.4f}\n"
        f"{s['current_qty']}: {data['total_quantity']:g}\n"
        f"{pnl_emoji} {s['realized_pnl']}: {sign}${pnl:,.2f}"
    )
    await _send(update, text)


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    alerts = get_today_alerts()
    if not alerts:
        await _send(update, s["no_alerts"])
        return

    lines = [f"{s['alerts_title']}\n"]
    for a in alerts:
        time_str = str(a["triggered_at"])[11:16]
        lines.append(f"[{time_str}] {a['symbol']} — {a['alert_type']}\n{a['message']}\n")

    await _send(update, "\n".join(lines).strip())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    sep = "━" * 19

    text = (
        f"{s['help_title']}\n"
        f"{sep}\n"
        f"{s['help_analysis_sec']}\n"
        f"/analyze SYMBOL — {s['help_analyze']}\n"
        f"/scan — {s['help_scan']}\n"
        f"\n"
        f"{s['help_watchlist_sec']}\n"
        f"/watchlist — {s['help_watchlist_cmd']}\n"
        f"/add SYMBOL CATEGORY — {s['help_add']}\n"
        f"/remove SYMBOL — {s['help_remove']}\n"
        f"\n"
        f"{s['help_trades_sec']}\n"
        f"/trade BUY|SELL SYMBOL QTY PRICE — {s['help_trade']}\n"
        f"/trades SYMBOL — {s['help_trades_cmd']}\n"
        f"/summary SYMBOL — {s['help_summary']}\n"
        f"\n"
        f"{s['help_tools_sec']}\n"
        f"/alerts — {s['help_alerts']}\n"
        f"/status — {s['help_status']}\n"
        f"/test — {s['help_test']}\n"
        f"/language he|en — {s['help_language']}\n"
        f"/help — {s['help_help']}\n"
        f"{sep}"
    )
    await _send(update, text)


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    await _send(update, STRINGS[lang]["scan_running"])
    chat_id = str(update.effective_chat.id)
    await run_morning_scan(context.bot, chat_id)


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    text = f"✅ {s['test_msg']} — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    print(f"[TELEGRAM OK] /test confirmed delivery to chat_id={update.effective_chat.id}")
    await _send(update, text)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    now_est    = datetime.now(_ET)
    is_weekend = now_est.weekday() >= 5
    open_      = is_market_open()

    if open_:
        status = s["market_open_hours"]
    elif is_weekend:
        status = s["market_closed_weekend"]
    else:
        status = s["market_closed_hours"]

    wl      = get_watchlist()
    total   = sum(len(v) for v in wl.values())

    vix_line = ""
    vix_data = get_current_price("^VIX")
    if vix_data:
        sign = "+" if vix_data["change_pct"] >= 0 else ""
        vix_line = f"\U0001f321 VIX: {vix_data['price']} ({sign}{vix_data['change_pct']}%)\n"

    alerts_today = get_today_alerts()
    muted        = get_muted_symbols(ALERT_COOLDOWN_HOURS)
    muted_str    = ", ".join(muted) if muted else s["none_label"]
    alert_line   = f"\U0001f514 {s['alerts_active_today']}: {len(alerts_today)}\n"
    muted_line   = f"\U0001f4f5 {s['muted_label'].format(hours=ALERT_COOLDOWN_HOURS)}: {muted_str}"

    text = (
        f"{s['status_title']}\n\n"
        f"{status}\n"
        f"{vix_line}"
        f"\U0001f4cb Watchlist: {total} {s['watchlist_stocks']} {len(wl)} {s['watchlist_cats']}\n"
        f"{alert_line}"
        f"{muted_line}"
    )
    await _send(update, text)


# ── Bot runner ────────────────────────────────────────────────────────────────

_BOT_COMMANDS = [
    BotCommand("analyze",   "ניתוח טכני מלא"),
    BotCommand("scan",      "סריקת מניות חמות"),
    BotCommand("watchlist", "הצג את כל המניות"),
    BotCommand("add",       "הוסף מניה לרשימה"),
    BotCommand("remove",    "הסר מניה"),
    BotCommand("trade",     "תעד עסקה"),
    BotCommand("trades",    "היסטוריית עסקאות"),
    BotCommand("summary",   "סיכום פוזיציה"),
    BotCommand("alerts",    "התראות היום"),
    BotCommand("status",    "סטטוס שוק"),
    BotCommand("test",      "בדיקת חיבור"),
    BotCommand("language",  "שנה שפה / Change language"),
    BotCommand("help",      "תפריט פקודות"),
]


def run_bot(token: str) -> None:
    init_db(WATCHLIST)

    async def post_init(application) -> None:
        await application.bot.set_my_commands(_BOT_COMMANDS)

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("language",  cmd_language))
    app.add_handler(CommandHandler("scan",      cmd_scan))
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

from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from agent.core import run_morning_scan
from analyzers.technical import full_analysis
from config import (
    ALERT_COOLDOWN_HOURS, AUTHORIZED_CHAT_IDS,
    CATEGORIES, TELEGRAM_ALLOW_WATCHLIST_APPLY,
    WATCHLIST, WATCHLIST_CHANGES_DEFAULT_LIMIT, WATCHLIST_CHANGES_MAX_LIMIT,
)
from data.fetcher import get_current_price, get_historical, is_market_open
from db.database import (
    add_to_watchlist,
    get_active_watchlist,
    get_evaluation_run,
    get_in_progress_evaluation_run,
    get_language,
    get_last_evaluation_run,
    get_muted_symbols,
    get_symbol_status,
    get_symbols_by_state,
    get_today_alerts,
    get_trade_summary,
    get_trades,
    get_watchlist,
    get_watchlist_summary,
    init_db,
    list_recent_evaluation_runs,
    log_alert,
    log_trade,
    remove_from_watchlist,
    run_initial_classification,
    set_language,
    update_symbol_state,
)
from services.watchlist_evaluator import explain_symbol, run_watchlist_evaluation
from services.watchlist_scheduler import can_start_evaluation_run, get_last_successful_scheduled_evaluation

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
        # help — Phase 9B-1 additions
        "help_start":               "התחלה",
        "help_general_sec":          "\U0001f9ed כללי",
        "help_alerts_sec":           "\U0001f514 התראות",
        "help_admin_hint":           "לרשימת פקודות טכניות/ניהול, הקלד /admin_help",
        "help_legacy_note":          "(alias ותיק)",
        "help_watchlist_active":     "הצג מניות Active",
        "help_watchlist_monitor":    "הצג מועמדים ב-Monitor",
        "help_watchlist_context":    "הצג ETF/מדדים בהקשר",
        "help_watchlist_ineligible": "הצג מניות לא זמינות זמנית",
        "help_watchlist_status":     "סטטוס מלא למניה בודדת",
        "help_refresh_watchlist":    "הרץ רענון watchlist בטוח (dry-run)",
        "help_watchlist_refresh_status": "סטטוס רענון watchlist אחרון",
        "help_watchlist_changes":    "שינויים אחרונים ב-watchlist",
        "admin_help_title":          "\U0001f6e0 פקודות ניהול/טכניות",
        # watchlist tiers
        "wl_active_title":       "Active Watchlist",
        "wl_monitor_title":      "Monitor Tier",
        "wl_context_title":      "ETF/Index Context",
        "wl_ineligible_title":   "Temporarily Ineligible",
        "wl_status_title":       "Symbol Status",
        "wl_not_found":          "Symbol not found in watchlist",
        "wl_usage":              "Usage: /watchlist_status SYMBOL",
        "wl_no_active":          "No symbols in Active tier.",
        "wl_no_ineligible":      "No temporarily ineligible symbols.",
        "wl_top_candidates":     "Top candidates",
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
        # help — Phase 9B-1 additions
        "help_start":               "Getting started",
        "help_general_sec":          "\U0001f9ed General",
        "help_alerts_sec":           "\U0001f514 Alerts",
        "help_admin_hint":           "For technical/admin commands, type /admin_help",
        "help_legacy_note":          "(legacy alias)",
        "help_watchlist_active":     "Show ACTIVE symbols",
        "help_watchlist_monitor":    "Show MONITOR candidates",
        "help_watchlist_context":    "Show ETF/index context symbols",
        "help_watchlist_ineligible": "Show temporarily ineligible symbols",
        "help_watchlist_status":     "Full status for one symbol",
        "help_refresh_watchlist":    "Run a safe watchlist refresh (dry-run)",
        "help_watchlist_refresh_status": "Last watchlist refresh status",
        "help_watchlist_changes":    "Recent watchlist changes",
        "admin_help_title":          "\U0001f6e0 Admin / Debug Commands",
        # watchlist tiers
        "wl_active_title":       "Active Watchlist",
        "wl_monitor_title":      "Monitor Tier",
        "wl_context_title":      "ETF/Index Context",
        "wl_ineligible_title":   "Temporarily Ineligible",
        "wl_status_title":       "Symbol Status",
        "wl_not_found":          "Symbol not found in watchlist",
        "wl_usage":              "Usage: /watchlist_status SYMBOL",
        "wl_no_active":          "No symbols in Active tier.",
        "wl_no_ineligible":      "No temporarily ineligible symbols.",
        "wl_top_candidates":     "Top candidates",
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


# Telegram's hard limit is 4096 characters; stay well under it so
# formatting/markdown overhead never tips a chunk over the real limit.
TELEGRAM_SAFE_CHUNK_LIMIT = 3500


def _split_into_chunks(text: str, limit: int = TELEGRAM_SAFE_CHUNK_LIMIT) -> list[str]:
    """
    Split text into chunks of at most `limit` characters, breaking on line
    boundaries wherever possible. A single line longer than `limit` is hard
    -split as a last resort (this should be rare — most callers build
    output line-by-line). Never returns an empty/whitespace-only chunk.
    """
    if not text or not text.strip():
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def _flush() -> None:
        if current:
            chunks.append("\n".join(current))
            current.clear()

    for line in text.split("\n"):
        if len(line) > limit:
            _flush()
            current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start:start + limit])
            continue

        added_len = len(line) + (1 if current else 0)
        if current and current_len + added_len > limit:
            _flush()
            current_len = 0
            added_len = len(line)

        current.append(line)
        current_len += added_len

    _flush()
    return [c for c in chunks if c.strip()]


async def _send(update: Update, text: str) -> None:
    """
    Send text to the chat, automatically splitting it into multiple
    Telegram-safe messages if it's too long. Never crashes the calling
    handler — a BadRequest (e.g. from an unexpected over-length chunk) is
    logged safely (no message content/secrets) and replaced with a short
    fallback notice instead of propagating.
    """
    for chunk in _split_into_chunks(text):
        try:
            await update.message.reply_text(chunk)
        except BadRequest as exc:
            print(f"[telegram] BadRequest sending message: {exc}")
            try:
                await update.message.reply_text(
                    "⚠️ Message too long to display. Try a more specific command "
                    "(e.g. add a number or symbol filter)."
                )
            except Exception:
                pass
            return


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
    update_symbol_state(symbol, 'MONITOR')
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
    """
    Summary-only by design — the watchlist can hold hundreds of symbols
    (it's seeded from config.py's full multi-category list), and dumping
    every symbol with a live price here previously produced a single
    Telegram message that could exceed the 4096-character limit and crash
    the handler with BadRequest("Message is too long"). Use the tier-
    specific commands below for the actual symbol lists, which are already
    bounded/paginated.
    """
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    summary = get_watchlist_summary()
    if not summary:
        await _send(update, s["watchlist_empty"])
        return

    lines = [
        s["watchlist_title"],
        "",
        f"ACTIVE: {summary.get('ACTIVE', 0)}",
        f"MONITOR: {summary.get('MONITOR', 0)}",
        f"ETF/Index context: {summary.get('ETF_INDEX_CONTEXT', 0)}",
        f"Temporarily ineligible: {summary.get('TEMPORARILY_INELIGIBLE', 0)}",
        f"User removed: {summary.get('USER_REMOVED', 0)}",
        "",
        "For details, use:",
        "/watchlist_active",
        "/watchlist_monitor",
        "/watchlist_context",
        "/watchlist_ineligible",
    ]
    await _send(update, "\n".join(lines))


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
    """
    Full, accurate command menu grouped by General / Analysis / Watchlist /
    Trades / Alerts (Phase 9B-1). Every command in this section is either
    listed here directly or pointed at via /admin_help — nothing registered
    in _BOT_COMMANDS/run_bot() should be undiscoverable from /help.

    Renamed commands (/watchlist_add, /watchlist_remove, /morning_scan) are
    shown as the primary name; their pre-existing counterparts (/add,
    /remove, /scan) still work identically (same handler function — see
    run_bot()) and are noted inline as "(legacy alias)" rather than removed,
    per the backward-compatibility requirement for this phase.
    """
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    sep = "━" * 19

    text = (
        f"{s['help_title']}\n"
        f"{sep}\n"
        f"{s['help_general_sec']}\n"
        f"/start — {s['help_start']}\n"
        f"/help — {s['help_help']}\n"
        f"/status — {s['help_status']}\n"
        f"/language he|en — {s['help_language']}\n"
        f"\n"
        f"{s['help_analysis_sec']}\n"
        f"/analyze SYMBOL — {s['help_analyze']}\n"
        f"/morning_scan — {s['help_scan']} {s['help_legacy_note']}: /scan\n"
        f"\n"
        f"{s['help_watchlist_sec']}\n"
        f"/watchlist — {s['help_watchlist_cmd']}\n"
        f"/watchlist_active — {s['help_watchlist_active']}\n"
        f"/watchlist_monitor — {s['help_watchlist_monitor']}\n"
        f"/watchlist_context — {s['help_watchlist_context']}\n"
        f"/watchlist_ineligible — {s['help_watchlist_ineligible']}\n"
        f"/watchlist_status SYMBOL — {s['help_watchlist_status']}\n"
        f"/watchlist_add SYMBOL CATEGORY — {s['help_add']} {s['help_legacy_note']}: /add\n"
        f"/watchlist_remove SYMBOL — {s['help_remove']} {s['help_legacy_note']}: /remove\n"
        f"/refresh_watchlist — {s['help_refresh_watchlist']}\n"
        f"\n"
        f"{s['help_trades_sec']}\n"
        f"/trade BUY|SELL SYMBOL QTY PRICE — {s['help_trade']}\n"
        f"/trades SYMBOL — {s['help_trades_cmd']}\n"
        f"/summary SYMBOL — {s['help_summary']}\n"
        f"\n"
        f"{s['help_alerts_sec']}\n"
        f"/alerts — {s['help_alerts']}\n"
        f"\n"
        f"{sep}\n"
        f"{s['help_admin_hint']}\n"
        f"{sep}"
    )
    await _send(update, text)


async def cmd_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Secondary help surface (Phase 9B-1) for technical/operational commands
    that don't belong in the main /help daily-use menu: connectivity check
    and watchlist-refresh-pipeline auditing tools. Read-only, same auth gate
    as every other command — this is not a privilege tier, just a less
    cluttered place for less-frequently-needed commands to live.
    """
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    sep = "━" * 19

    text = (
        f"{s['admin_help_title']}\n"
        f"{sep}\n"
        f"/test — {s['help_test']}\n"
        f"/watchlist_refresh_status — {s['help_watchlist_refresh_status']}\n"
        f"/watchlist_changes — {s['help_watchlist_changes']}\n"
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


# ── Watchlist tier commands ───────────────────────────────────────────────────

async def cmd_watchlist_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    wl = get_active_watchlist()
    if not wl:
        await _send(update, s["wl_no_active"])
        return
    lines = [f"{s['wl_active_title']}\n"]
    for cat, symbols in sorted(wl.items()):
        lines.append(f"\U0001f4c2 {cat}")
        for sym in symbols:
            status = get_symbol_status(sym)
            score = status.get('relevance_score') if status else None
            score_str = f" [{score}]" if score is not None else ""
            lines.append(f"  {sym}{score_str}")
        lines.append("")
    await _send(update, "\n".join(lines[:35]).strip())


async def cmd_watchlist_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    MONITOR can hold hundreds of symbols, so this never lists all of them —
    only the top-N scored candidates (default 10, override with e.g.
    "/watchlist_monitor 50", capped at 100 to stay well under the safe
    Telegram chunk limit even before _send()'s own splitting kicks in).
    """
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]

    limit = 10
    if context.args and context.args[0].isdigit():
        limit = min(int(context.args[0]), 100)

    symbols = get_symbols_by_state('MONITOR')
    lines = [f"{s['wl_monitor_title']} — {len(symbols)} symbols"]
    scored = []
    for sym in symbols:
        status = get_symbol_status(sym)
        if status and status.get('relevance_score') is not None:
            scored.append((sym, status['relevance_score']))
    scored.sort(key=lambda x: x[1], reverse=True)
    if scored:
        lines.append(f"\n{s['wl_top_candidates']} (showing {min(limit, len(scored))} of {len(scored)} scored):")
        for sym, score in scored[:limit]:
            lines.append(f"  {sym} [{score}]")
        remaining = len(scored) - limit
        if remaining > 0:
            lines.append(f"  (+{remaining} more — use /watchlist_monitor {min(limit * 2, 100)} for more)")
    await _send(update, "\n".join(lines))


async def cmd_watchlist_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    symbols = get_symbols_by_state('ETF_INDEX_CONTEXT')
    lines = [f"{s['wl_context_title']} ({len(symbols)} symbols)"]
    for sym in sorted(symbols)[:25]:
        lines.append(f"  {sym}")
    await _send(update, "\n".join(lines))


async def cmd_watchlist_ineligible(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    symbols = get_symbols_by_state('TEMPORARILY_INELIGIBLE')
    if not symbols:
        await _send(update, s["wl_no_ineligible"])
        return
    lines = [f"{s['wl_ineligible_title']} ({len(symbols)})"]
    for sym in sorted(symbols)[:25]:
        status = get_symbol_status(sym)
        reason = status.get('exclusion_reason', '') if status else ''
        lines.append(f"  {sym}: {reason}")
    await _send(update, "\n".join(lines))


_OPPORTUNITY_SIGNAL_LABELS: dict[str, str] = {
    "price_above_ema150":      "EMA150 trend",
    "ema150_above_ema200":     "EMA150>EMA200",
    "macd_bullish_crossover":  "MACD bull cross",
    "rsi_healthy_range":       "RSI healthy zone",
    "rsi_acceptable_zone":     "RSI acceptable zone",
    "volume_spike":            "Volume spike",
    "stoch_rsi_bullish_cross": "Stoch RSI cross",
    "above_vwap":              "Above VWAP",
}


def _format_watchlist_status(symbol: str, status: dict, explanation: dict | None) -> str:
    """
    Builds the full explainability view for one symbol: persisted
    lifecycle state, a live (never-persisted) relevance-score breakdown,
    a live opportunity-score breakdown with veto reasons, and a fixed
    disclaimer. `explanation` is the dict returned by
    services.watchlist_evaluator.explain_symbol() (or None if that call
    itself failed unexpectedly — the lifecycle section is still shown).
    """
    cats = ", ".join(status.get("categories", [])) or "N/A"
    lines = [
        f"\U0001f4ca Watchlist Status: {symbol}",
        "",
        "Lifecycle (persisted):",
        f"  State: {status.get('wl_state', 'N/A')}  Type: {status.get('security_type', 'N/A')}",
        f"  Categories: {cats}  Enabled: {status.get('enabled', 'N/A')}",
        f"  Persisted score: {status.get('relevance_score', 'N/A')} (only set by apply mode, not dry-run)",
        f"  Promote streak: {status.get('consec_promote_count', 0)}  "
        f"Demote streak: {status.get('consec_demote_count', 0)}  Dwell: {status.get('dwell_days', 0)}d",
        f"  Last evaluated: {status.get('last_evaluated') or 'never'}",
        f"  Exclusion reason: {status.get('exclusion_reason') or 'none'}",
    ]

    if explanation is None:
        lines += ["", "Live breakdown unavailable (internal error fetching live data)."]
    elif not explanation.get("data_ok"):
        lines += ["", f"Live data fetch failed: {explanation.get('failure_reason') or 'unknown reason'}",
                  "Relevance/opportunity breakdown unavailable this check."]
    else:
        rel = explanation["relevance"]
        comp = rel["components"]
        w = rel["weights_pct"]
        lines += [
            "",
            f"Relevance score (live, NOT persisted): {rel['score']}/100",
            f"  Data quality ({w['data_quality']}%): {comp['data_quality']:.2f}   "
            f"Liquidity ({w['liquidity']}%): {comp['liquidity']:.2f}",
            f"  Trend ({w['trend']}%): {comp['trend']:.2f}   Momentum ({w['momentum']}%): {comp['momentum']:.2f}",
            f"  Proximity ({w['proximity']}%): {comp['proximity']:.2f}   Volatility ({w['volatility']}%): {comp['volatility']:.2f}",
            f"  Would be: {explanation['would_be_state']} — {explanation['would_be_reason']}",
        ]

        opp = explanation.get("opportunity")
        if opp is None:
            lines += ["", "Live opportunity score unavailable (indicator calculation failed)."]
        else:
            fired = [_OPPORTUNITY_SIGNAL_LABELS[k] for k, v in opp["signals"].items() if v and k in _OPPORTUNITY_SIGNAL_LABELS]
            ema150_str = f"{opp['ema150']:,.2f}" if opp["ema150"] is not None else "N/A (needs 150+ days history)"
            ema200_str = f"{opp['ema200']:,.2f}" if opp["ema200"] is not None else "N/A (needs 200+ days history)"
            lines += [
                "",
                f"Live opportunity score: {opp['score']}/100 — {opp['verdict']}",
                f"  RSI: {opp['rsi']}   EMA150: {ema150_str}   EMA200: {ema200_str}",
                f"  Signals fired: {', '.join(fired) if fired else 'none'}",
                f"  Veto: {opp['vetoed'] or 'none'}",
            ]

    lines += [
        "",
        "⚠️ Screening/alert score only — not a verified BUY recommendation.",
        "No fundamentals, news, earnings calendar, or backtest are used.",
    ]
    return "\n".join(lines)


async def cmd_watchlist_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    lang = get_language(str(update.effective_chat.id))
    s = STRINGS[lang]
    if not context.args:
        await _send(update, s["wl_usage"])
        return
    symbol = context.args[0].upper()
    status = get_symbol_status(symbol)
    if not status:
        await _send(update, f"{s['wl_not_found']}: {symbol}")
        return

    try:
        explanation = explain_symbol(symbol)
    except Exception as exc:
        print(f"[watchlist_status] explain_symbol failed for {symbol}: {type(exc).__name__}")
        explanation = None

    await _send(update, _format_watchlist_status(symbol, status, explanation))


# ── Watchlist evaluation refresh (Phase 7) ────────────────────────────────────
#
# /refresh_watchlist, /watchlist_refresh_status, /watchlist_changes.
# Default is always dry-run. Apply requires TELEGRAM_ALLOW_WATCHLIST_APPLY=true
# in config AND the literal word "confirm" in the command — neither alone
# is enough. No exception is ever shown raw to the user; everything is
# caught and logged to stdout (never to Telegram) with only the exception
# type name surfaced, never secrets/paths/stack traces.

def _format_symbol_list(symbols: list[str], limit: int) -> str:
    if not symbols:
        return "—"
    shown = symbols[:limit]
    text = ", ".join(shown)
    remaining = len(symbols) - len(shown)
    if remaining > 0:
        text += f" (+{remaining} more)"
    return text


def _format_refresh_summary(result, apply_mode: bool, run_status: str) -> str:
    if result.fatal_error:
        return (
            "⚠️ Watchlist refresh failed\n\n"
            f"Run ID: {result.run_id}\n"
            "An internal error occurred and no watchlist state was changed.\n"
            "Check the server logs for details."
        )

    if run_status == "partial_failure":
        header = "⚠️ Watchlist refresh completed — PARTIAL FAILURE"
    else:
        header = f"\U0001f4ca Watchlist refresh completed — {'APPLY' if apply_mode else 'DRY RUN'}"

    lines = [
        header,
        "",
        f"Run ID: {result.run_id}",
        f"Status: {run_status}",
        f"Evaluated: {result.total_symbols_evaluated}",
        f"Skipped: {result.total_symbols_skipped}",
        f"Failed: {result.total_symbols_failed}",
        "",
        f"ACTIVE: {result.active_before} → {result.active_after}",
        f"MONITOR: {result.monitor_before} → {result.monitor_after}",
        f"Context: {result.context_count}",
        f"Unavailable: {result.temporarily_ineligible_after}",
        "",
        f"{'Promotions' if apply_mode else 'Proposed promotions'}: {len(result.proposed_promotions)}",
        f"{'Demotions' if apply_mode else 'Proposed demotions'}: {len(result.proposed_demotions)}",
        f"Provider errors: {result.provider_error_count}",
        f"Runtime: {result.duration_seconds:.1f}s",
        "",
    ]
    if result.provider_degraded:
        lines.append("Provider degraded: yes")
        lines.append("Demotions suppressed to protect the current ACTIVE list.")
    if apply_mode and result.applied:
        lines.append("Watchlist states WERE changed (apply mode).")
        lines.append(
            f"To undo: python scripts/rollback_evaluation_run.py --run-id {result.run_id} --yes"
        )
    else:
        lines.append("No watchlist states were changed.")
    return "\n".join(lines)


async def cmd_refresh_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return

    args = [a.lower() for a in (context.args or [])]
    mode = args[0] if args else "dry_run"
    if mode not in ("dry_run", "apply"):
        await _send(update, "Usage: /refresh_watchlist [dry_run|apply confirm]")
        return

    apply_mode = mode == "apply"
    if apply_mode:
        if not TELEGRAM_ALLOW_WATCHLIST_APPLY:
            await _send(update, (
                "Apply mode is disabled for Telegram.\n\n"
                "Use dry-run first:\n"
                "`/refresh_watchlist`\n\n"
                "To enable Telegram apply, set TELEGRAM_ALLOW_WATCHLIST_APPLY=true and restart the bot."
            ))
            return
        if "confirm" not in args:
            await _send(update, (
                "Apply mode requires explicit confirmation.\n\n"
                "Use:\n`/refresh_watchlist apply confirm`"
            ))
            return

    guard_ok, guard_reason = can_start_evaluation_run()
    if not guard_ok:
        await _send(update, f"⏳ A watchlist refresh is already in progress.\n{guard_reason}")
        return

    await _send(update, f"\U0001f504 Starting watchlist refresh ({'apply' if apply_mode else 'dry-run'})...")

    try:
        result = run_watchlist_evaluation(apply=apply_mode, triggered_by="telegram")
    except Exception as exc:  # never show a raw exception/stack trace to the user
        print(f"[refresh_watchlist] unexpected error: {type(exc).__name__}")
        await _send(update, "⚠️ Watchlist refresh failed unexpectedly. Check server logs.")
        return

    run = get_evaluation_run(result.run_id)
    run_status = run["status"] if run else ("failed" if result.fatal_error else "success")
    await _send(update, _format_refresh_summary(result, apply_mode, run_status))


async def cmd_watchlist_refresh_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return

    run = get_last_evaluation_run()
    if run is None:
        await _send(update, "No watchlist evaluation has run yet.")
        return

    in_progress = get_in_progress_evaluation_run()
    metadata = run.get("metadata_json") or {}
    last_scheduled = get_last_successful_scheduled_evaluation()

    lines = [
        "\U0001f4ca Watchlist Refresh Status",
        "",
        f"Run ID: {run['run_id']}",
        f"Type: {run['run_type']} ({'dry-run' if run['dry_run'] else 'apply'})",
        f"Status: {run['status']}",
        f"Started: {run['started_at']} UTC",
        f"Completed: {run['completed_at'] or 'in progress'} UTC" if run["completed_at"] else "Completed: in progress",
        f"Duration: {run['duration_seconds']:.1f}s" if run["duration_seconds"] is not None else "Duration: n/a",
        "",
        f"Evaluated: {run['total_symbols_evaluated']}  Skipped: {run['total_symbols_skipped']}  "
        f"Failed: {run['total_symbols_failed']}",
        f"Provider errors: {run['provider_error_count']}  Stale data: {run['stale_data_count']}  "
        f"Invalid symbols: {run['invalid_symbol_count']}",
        "",
        f"Promotions: {run['promotions_count']}  Demotions: {run['demotions_count']}  "
        f"Recovered: {run['recovered_count']}  Newly ineligible: {run['newly_ineligible_count']}",
        f"ACTIVE: {run['active_before']} → {run['active_after']}",
        f"MONITOR: {run['monitor_before']} → {run['monitor_after']}",
        "",
        f"Provider degraded: {'yes' if metadata.get('provider_degraded') else 'no'}",
        f"Another run in progress: {'yes (run ' + str(in_progress['run_id']) + ')' if in_progress else 'no'}",
    ]
    if last_scheduled:
        lines.append(f"Last successful scheduled run: run {last_scheduled['run_id']} "
                      f"({last_scheduled['started_at']} UTC)")
    else:
        lines.append("Last successful scheduled run: none yet")

    await _send(update, "\n".join(lines))


async def cmd_watchlist_changes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return

    args = context.args or []
    limit = WATCHLIST_CHANGES_DEFAULT_LIMIT
    run = None

    if args and args[0].lower() == "run":
        if len(args) < 2 or not args[1].isdigit():
            await _send(update, "Usage: /watchlist_changes run RUN_ID")
            return
        run = get_evaluation_run(int(args[1]))
        if run is None:
            await _send(update, f"Run {args[1]} not found.")
            return
    elif args and args[0].isdigit():
        limit = min(int(args[0]), WATCHLIST_CHANGES_MAX_LIMIT)
    elif args:
        await _send(update, "Usage: /watchlist_changes [N] | /watchlist_changes run RUN_ID")
        return

    if run is None:
        recent = list_recent_evaluation_runs(limit=100)
        run = next((r for r in recent if not r["dry_run"]), None)
        mode_label = "APPLIED"
        if run is None:
            run = next((r for r in recent if r["dry_run"]), None)
            mode_label = "DRY RUN, proposed only"
        if run is None:
            await _send(update, "No evaluation runs found yet.")
            return
    else:
        mode_label = "DRY RUN, proposed only" if run["dry_run"] else "APPLIED"

    metadata = run.get("metadata_json") or {}
    promotions = metadata.get("proposed_promotions", [])
    demotions = metadata.get("proposed_demotions", [])
    recoveries = metadata.get("proposed_recoveries", [])
    ineligible = metadata.get("proposed_ineligible", [])

    lines = [
        f"\U0001f4cb Watchlist Changes — Run {run['run_id']} ({mode_label})",
        "",
        f"Promotions ({len(promotions)}): {_format_symbol_list(promotions, limit)}",
        f"Demotions ({len(demotions)}): {_format_symbol_list(demotions, limit)}",
        f"Recovered ({len(recoveries)}): {_format_symbol_list(recoveries, limit)}",
        f"Newly ineligible ({len(ineligible)}): {_format_symbol_list(ineligible, limit)}",
        "",
        f"Showing up to {limit} symbols per category.",
    ]
    if run["dry_run"]:
        lines.append("This run was a dry-run — nothing was written to the watchlist.")
    else:
        lines.append(
            f"To undo this run: python scripts/rollback_evaluation_run.py --run-id {run['run_id']} --yes"
        )
    await _send(update, "\n".join(lines))


# ── Bot runner ────────────────────────────────────────────────────────────────

# Every entry here must correspond to a real CommandHandler registered
# below in run_bot() -- this is the full inventory, not a curated subset.
_BOT_COMMANDS = [
    BotCommand("start",      "התחלה"),
    BotCommand("help",       "תפריט פקודות"),
    BotCommand("language",   "שנה שפה / Change language"),
    BotCommand("scan",       "סריקת מניות חמות"),
    BotCommand("test",       "בדיקת חיבור"),
    BotCommand("analyze",    "ניתוח טכני מלא"),
    BotCommand("add",        "הוסף מניה לרשימה"),
    BotCommand("remove",     "הסר מניה"),
    BotCommand("watchlist",  "סיכום רשימת מעקב"),
    BotCommand("trade",      "תעד עסקה"),
    BotCommand("trades",     "היסטוריית עסקאות"),
    BotCommand("summary",    "סיכום פוזיציה"),
    BotCommand("alerts",     "התראות היום"),
    BotCommand("status",     "סטטוס שוק"),
    BotCommand("watchlist_active",         "Show ACTIVE symbols"),
    BotCommand("watchlist_monitor",        "Show MONITOR symbols"),
    BotCommand("watchlist_context",        "Show ETF/index context symbols"),
    BotCommand("watchlist_ineligible",     "Show temporarily ineligible symbols"),
    BotCommand("watchlist_status",         "Show status for one symbol"),
    BotCommand("refresh_watchlist",        "Run a safe watchlist refresh (dry-run)"),
    BotCommand("watchlist_refresh_status", "Show last refresh status"),
    BotCommand("watchlist_changes",        "Show recent watchlist changes"),
    # Phase 9B-1: renamed aliases + admin help. Old names above are kept
    # registered and in this menu unchanged -- these are additive, not
    # replacements (see cmd_help()'s docstring and run_bot() below).
    BotCommand("watchlist_add",    "הוסף מניה לרשימה (alias ל-/add)"),
    BotCommand("watchlist_remove", "הסר מניה (alias ל-/remove)"),
    BotCommand("morning_scan",     "סריקת מניות חמות (alias ל-/scan)"),
    BotCommand("admin_help",       "פקודות ניהול/טכניות"),
]


async def _register_bot_commands(application) -> None:
    """
    Registers the Telegram hamburger/menu command list. Best-effort: a
    failure here (e.g. a transient Telegram API error) must never prevent
    the bot from starting and serving commands. Never logs secrets,
    tokens, chat IDs, DB paths, or a stack trace -- only the exception
    type name.
    """
    try:
        await application.bot.set_my_commands(_BOT_COMMANDS)
    except Exception as exc:
        print(f"[telegram] Failed to register command menu: {type(exc).__name__}")


def run_bot(token: str) -> None:
    init_db(WATCHLIST)
    run_initial_classification(WATCHLIST)

    app = ApplicationBuilder().token(token).post_init(_register_bot_commands).build()

    app.add_handler(CommandHandler("start",                cmd_start))
    app.add_handler(CommandHandler("help",                 cmd_help))
    app.add_handler(CommandHandler("language",             cmd_language))
    app.add_handler(CommandHandler("scan",                 cmd_scan))
    app.add_handler(CommandHandler("test",                 cmd_test))
    app.add_handler(CommandHandler("analyze",              cmd_analyze))
    app.add_handler(CommandHandler("add",                  cmd_add))
    app.add_handler(CommandHandler("remove",               cmd_remove))
    app.add_handler(CommandHandler("watchlist",            cmd_watchlist))
    app.add_handler(CommandHandler("trade",                cmd_trade))
    app.add_handler(CommandHandler("trades",               cmd_trades))
    app.add_handler(CommandHandler("summary",              cmd_summary))
    app.add_handler(CommandHandler("alerts",               cmd_alerts))
    app.add_handler(CommandHandler("status",               cmd_status))
    app.add_handler(CommandHandler("watchlist_active",     cmd_watchlist_active))
    app.add_handler(CommandHandler("watchlist_monitor",    cmd_watchlist_monitor))
    app.add_handler(CommandHandler("watchlist_context",    cmd_watchlist_context))
    app.add_handler(CommandHandler("watchlist_ineligible", cmd_watchlist_ineligible))
    app.add_handler(CommandHandler("watchlist_status",     cmd_watchlist_status))
    app.add_handler(CommandHandler("refresh_watchlist",         cmd_refresh_watchlist))
    app.add_handler(CommandHandler("watchlist_refresh_status",  cmd_watchlist_refresh_status))
    app.add_handler(CommandHandler("watchlist_changes",         cmd_watchlist_changes))

    # Phase 9B-1: renamed aliases -- same handler functions as their legacy
    # counterparts above, so behavior is identical by construction. The
    # legacy names (/add, /remove, /scan) remain registered and functional;
    # nothing here changes cmd_add/cmd_remove/cmd_scan themselves.
    app.add_handler(CommandHandler("watchlist_add",    cmd_add))
    app.add_handler(CommandHandler("watchlist_remove", cmd_remove))
    app.add_handler(CommandHandler("morning_scan",     cmd_scan))
    app.add_handler(CommandHandler("admin_help",       cmd_admin_help))

    app.run_polling()

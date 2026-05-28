"""
StockSage — config.py
הגדרות מרכזיות, Watchlist לפי קטגוריות, וסף התראות
"""

from dotenv import load_dotenv
import os

load_dotenv()

# ─────────────────────────────────────────────
#  Telegram
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ─────────────────────────────────────────────
#  API Keys
# ─────────────────────────────────────────────
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────
#  Watchlist — מחולק לקטגוריות
# ─────────────────────────────────────────────
WATCHLIST: dict[str, list[str]] = {

    "מדדים": [
        "^GSPC",   # S&P 500
        "^IXIC",   # Nasdaq Composite
        "QQQ",     # Nasdaq 100 ETF
        "^DJI",    # Dow Jones
        "^RUT",    # Russell 2000
        "^VIX",    # מדד פחד
    ],

    "קריפטו": [
        "BTC-USD",
        "ETH-USD",
    ],

    "ETFs": [
        "SPY",     # S&P 500
        "VOO",     # Vanguard S&P 500
        "QQQ",     # Nasdaq 100
        "VGT",     # Tech
        "XLK",     # Tech Sector
        "SOXX",    # Semiconductors
        "CIBR",    # Cybersecurity
        "ARKK",    # ARK Innovation
        "SCHG",    # Large-Cap Growth
        "UFO",     # Space
        "NUKZ",    # Nuclear
    ],

    "AI & Semiconductors": [
        "NVDA",    # מוביל AI
        "AMD",
        "INTC",    # +370% ב-12 חודשים
        "AVGO",    # Broadcom
        "ARM",     # Arm Holdings
        "QCOM",
        "MRVL",
        "ANET",    # Data center networking
    ],

    "מגה טק": [
        "GOOGL",   # YTD: +27.2%
        "AMZN",    # YTD: +17.5%
        "MSFT",
        "META",
        "AAPL",
        "TSLA",
        "CBRS",    # Cerebras — IPO חם
    ],

    "ענן ותוכנה": [
        "SNOW",    # YTD: +46.9%
        "PLTR",    # AI/Defense
        "DDOG",    # Cloud monitoring
        "NET",     # Cloudflare
        "CRM",     # Salesforce
    ],

    "סייבר": [
        "CRWD",    # CrowdStrike
        "PANW",    # Palo Alto
        "FTNT",    # Fortinet
        "ZS",      # Zscaler
        "S",       # SentinelOne
    ],

    "תשתיות Data Center": [
        "EQIX",    # Equinix REIT
        "DLR",     # Digital Realty
        "IRM",     # Iron Mountain
        "VRT",     # Vertiv
        "APLD",    # Applied Digital
        "DOCN",    # YTD: +229%
        "GLW",     # Corning — Nvidia partnership
        "ETN",     # Eaton
        "MOD",     # Modine
        "CSCO",    # Cisco
    ],

    "חלל": [
        "RKLB",    # Rocket Lab 🔥
        "ASTS",    # AST SpaceMobile
        "LUNR",    # Intuitive Machines
        "LMT",     # Lockheed Martin
        "LHX",     # L3Harris
        "PL",      # Planet Labs
        "BA",      # Boeing
        "NOC",     # Northrop Grumman
    ],

    "אנרגיה": [
        "XOM",     # ExxonMobil
        "CVX",     # Chevron
        "COP",     # ConocoPhillips
        "OXY",     # Buffett pick
    ],

    "גרעין": [
        "CEG",     # Constellation Energy
        "VST",     # Vistra
        "GEV",     # GE Vernova
        "OKLO",    # Oklo SMR
        "SMR",     # NuScale
        "CCJ",     # Cameco — Uranium
        "SO",      # Southern Company
        "BEP",     # Brookfield Renewable
    ],

    "אנרגיה ירוקה": [
        "BE",      # Bloom Energy YTD: +249% 🔥
        "ENPH",    # Enphase
        "FSLR",    # First Solar
        "NEE",     # NextEra
    ],

    "פיננסים": [
        "JPM",     # JPMorgan
        "GS",      # Goldman Sachs
        "MSTR",    # MicroStrategy — Bitcoin play
    ],
}

# ─────────────────────────────────────────────
#  הגדרות התראות
# ─────────────────────────────────────────────

# ציון מינימלי לשליחת התראה טכנית (BUY או STRONG BUY)
ALERT_MIN_SCORE: int = 70

# ─────────────────────────────────────────────
#  סריקת בוקר
# ─────────────────────────────────────────────

# ציון מינימלי להכנסה לסריקת הבוקר (WEAK BUY ומעלה)
SCAN_MIN_SCORE: int = 50

# כמה מניות מובילות לשלוח בסריקת הבוקר
SCAN_TOP_N: int = 5

# שעת פתיחת השוק האמריקאי לפי שעון ישראל (16:35)
MARKET_OPEN_HOUR_IL: int = 16
MARKET_OPEN_MIN_IL: int = 35

# התראה אם מניה זזה יותר מ-X% ביום
ALERT_THRESHOLD_PCT: float = 3.0

# התראה אם מדד זז יותר מ-X% (סף נמוך יותר)
INDEX_ALERT_THRESHOLD_PCT: float = 1.5

# כמה פעמים בשעה לבדוק (כל 15 דק' = 4 פעמים)
CHECK_INTERVAL_MINUTES: int = 15

# שעות פעילות (לפי שעון ישראל — שוק אמריקאי פתוח 16:30–23:00)
MARKET_OPEN_HOUR: int = 16
MARKET_CLOSE_HOUR: int = 23

# ─────────────────────────────────────────────
#  הגדרות ניתוח טכני
# ─────────────────────────────────────────────
RSI_PERIOD: int = 14
RSI_OVERSOLD: int = 30      # מתחת → התראת קנייה
RSI_OVERBOUGHT: int = 70    # מעל   → התראת מכירה
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL: int = 9

# ─────────────────────────────────────────────
#  יומן מסחר
# ─────────────────────────────────────────────
TRADE_LOG_DB: str = "db/stocksage.db"

# ─────────────────────────────────────────────
#  קטגוריות — לשימוש הוספת מניה מהבוט
#  /add AAPL "מגה טק"
# ─────────────────────────────────────────────
CATEGORIES: list[str] = list(WATCHLIST.keys())
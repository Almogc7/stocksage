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

# Comma-separated list of authorized Telegram chat IDs allowed to issue bot
# commands. Set this to your own chat ID (same value as TELEGRAM_CHAT_ID for
# a personal bot). Leave the env var unset or empty to fall back to
# TELEGRAM_CHAT_ID only. The bot will silently ignore all other users.
#
# Example .env entry:
#   AUTHORIZED_CHAT_IDS=123456789
#
# Multiple users (e.g. family members sharing a bot):
#   AUTHORIZED_CHAT_IDS=123456789,987654321
_raw_auth_ids = os.getenv("AUTHORIZED_CHAT_IDS", "")
if _raw_auth_ids.strip():
    AUTHORIZED_CHAT_IDS: frozenset[str] = frozenset(
        cid.strip() for cid in _raw_auth_ids.split(",") if cid.strip()
    )
elif TELEGRAM_CHAT_ID:
    # Fall back to the outbound chat ID — correct for a personal bot.
    AUTHORIZED_CHAT_IDS = frozenset([str(TELEGRAM_CHAT_ID).strip()])
else:
    # No configuration at all — bot will reject every command until configured.
    AUTHORIZED_CHAT_IDS = frozenset()

# ─────────────────────────────────────────────
#  Reliability / monitoring
# ─────────────────────────────────────────────
# Dead-man's switch: this URL receives a GET at the end of every SUCCESSFUL
# agent check cycle (run_checks), market open or closed — the signal means
# "the scheduler daemon is alive", not "the market was scanned". If the
# daemon thread dies or every cycle starts failing, pings stop and the
# monitoring service alerts you.
#
# Setup (healthchecks.io, free tier):
#   1. Create an account, create a Check, set Period=15 min, Grace=15 min.
#   2. Copy its ping URL (https://hc-ping.com/<uuid>) into .env:
#        HEALTHCHECK_PING_URL=https://hc-ping.com/your-uuid-here
# Leave unset/empty to disable pinging entirely (no-op).
HEALTHCHECK_PING_URL: str = os.getenv("HEALTHCHECK_PING_URL", "").strip()

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
        # --- Original ---
        "NVDA",    # מוביל AI
        "AMD",
        "INTC",
        "AVGO",    # Broadcom
        "ARM",     # Arm Holdings
        "QCOM",
        "MRVL",
        "ANET",    # Data center networking
        # --- SOXX additions ---
        "SWKS",    # Skyworks Solutions
        "QRVO",    # Qorvo
        "NXPI",    # NXP Semiconductors
        "MPWR",    # Monolithic Power
        "STM",     # STMicroelectronics
        "MU",      # Micron Technology
        "ONTO",    # Onto Innovation
        "ON",      # ON Semiconductor
        "ASX",     # ASE Technology
        "ASML",    # ASML Holding
        "AMAT",    # Applied Materials
        "LSCC",    # Lattice Semiconductor
        "ENTG",    # Entegris
        "MKSI",    # MKS Instruments
        "LRCX",    # Lam Research
        "TER",     # Teradyne
        "KLAC",    # KLA Corporation
        "TSM",     # Taiwan Semiconductor
        "MCHP",    # Microchip Technology
        "TXN",     # Texas Instruments
        "ADI",     # Analog Devices
        "OLED",    # Universal Display
        "UMC",     # United Microelectronics
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
        # --- Original ---
        "SNOW",    # Snowflake
        "PLTR",    # AI/Defense
        "DDOG",    # Cloud monitoring
        "NET",     # Cloudflare
        "CRM",     # Salesforce
        # --- IGV additions ---
        "ORCL",    # Oracle
        "ADBE",    # Adobe
        "INTU",    # Intuit
        "CDNS",    # Cadence Design
        "SNPS",    # Synopsys
        "NOW",     # ServiceNow
        "ADSK",    # Autodesk
        "EA",      # Electronic Arts
        "TTWO",    # Take-Two Interactive
        "ROP",     # Roper Technologies
        "ZM",      # Zoom
        "WDAY",    # Workday
        "FICO",    # Fair Isaac
        "PTC",     # PTC Inc
        "TEAM",    # Atlassian
        "TRMB",    # Trimble
        "TYL",     # Tyler Technologies
        "HUBS",    # HubSpot
        "NTNX",    # Nutanix
        "DT",      # Dynatrace
        "GWRE",    # Guidewire
        "GEN",     # Gen Digital
        "IOT",     # Samsara
        "DOCU",    # DocuSign
        "RBRK",    # Rubrik
        "U",       # Unity Software
        "AUR",     # Aurora Innovation
        "MANH",    # Manhattan Associates
        "PCOR",    # Procore Technologies
        "DSGX",    # Descartes Systems
        "OTEX",    # OpenText
        "YOU",     # Clear Secure
        "BSY",     # Bentley Systems
        "ACIW",    # ACI Worldwide
        "ESTC",    # Elastic
        "PATH",    # UiPath
        "CVLT",    # Commvault
        "DBX",     # Dropbox
        "APPF",    # AppFolio
        "SOUN",    # SoundHound AI
        "GTLB",    # GitLab
        "BOX",     # Box Inc
        "DLB",     # Dolby Laboratories
        "ZETA",    # Zeta Global
        "LIF",     # Life360
        "RNG",     # RingCentral
        "QLYS",    # Qualys
        "VRNS",    # Varonis
        "PEGA",    # Pegasystems
        "BB",      # BlackBerry
        "BILL",    # Bill.com
        "QTWO",    # Q2 Holdings
        "CCC",     # Claros Mortgage (IGV)
        "ADEA",    # Adeia
        "TDC",     # Teradata
        "WK",      # Workiva
        "ALRM",    # Alarm.com
        "KVYO",    # Klaviyo
        "BRZE",    # Braze
        "SPSC",    # SPS Commerce
        "ATEN",    # A10 Networks
        "RAMP",    # LiveRamp
        "FRSH",    # Freshworks
        "AGYS",    # Agilysys
        "NCNO",    # nCino
        "FIVN",    # Five9
        "NN",      # NextNav
        "BL",      # BlackLine
        "AVPT",    # AvePoint
        "ALKT",    # Alkami Technology
        "BLKB",    # Blackbaud
        "INTA",    # Intapp
        "PRGS",    # Progress Software
        "LSPD",    # Lightspeed Commerce
        "AI",      # C3.ai
        "VYX",     # NCR Voyix
        "VERX",    # Vertex Inc
        "APPN",    # Appian
        "PD",      # PagerDuty
        "CXM",     # Sprinklr
        "ASAN",    # Asana
        "PAR",     # PAR Technology
        "RPD",     # Rapid7
        "NABL",    # N-able
    ],

    "סייבר": [
        "CRWD",    # CrowdStrike
        "PANW",    # Palo Alto
        "FTNT",    # Fortinet
        "ZS",      # Zscaler
        "S",       # SentinelOne
        "TENB",    # Tenable
        "DDOG",    # Datadog (monitoring + security)
    ],

    "תשתיות Data Center": [
        "EQIX",    # Equinix REIT
        "DLR",     # Digital Realty
        "IRM",     # Iron Mountain
        "VRT",     # Vertiv
        "APLD",    # Applied Digital
        "DOCN",    # DigitalOcean
        "GLW",     # Corning — Nvidia partnership
        "ETN",     # Eaton
        "MOD",     # Modine
        "CSCO",    # Cisco
    ],

    "חלל": [
        # --- Original ---
        "RKLB",    # Rocket Lab
        "ASTS",    # AST SpaceMobile
        "LUNR",    # Intuitive Machines
        "LMT",     # Lockheed Martin
        "LHX",     # L3Harris
        "PL",      # Planet Labs
        "BA",      # Boeing
        "NOC",     # Northrop Grumman
        # --- Defense additions ---
        "ESLT",    # Elbit Systems
        "SWBI",    # Smith & Wesson
        "AVAV",    # AeroVironment
        "KTOS",    # Kratos Defense
        "RTX",     # Raytheon Technologies
        "HWM",     # Howmet Aerospace
        "WWD",     # Woodward
        "HII",     # Huntington Ingalls
        "AXON",    # Axon Enterprise
        "HXL",     # Hexcel
        "CW",      # Curtiss-Wright
        "GD",      # General Dynamics
        "NPK",     # National Presto (defense)
        "PSN",     # Parsons Corporation
        "HEI",     # HEICO
        "BWXT",    # BWX Technologies
        "RBC",     # RBC Bearings
        "TDG",     # TransDigm Group
        "AIR",     # AAR Corp
        "ONDS",    # Ondas Holdings
        "RGR",     # Sturm Ruger
        "MRCY",    # Mercury Systems
        "TXT",     # Textron
    ],

    "אנרגיה": [
        "XOM",     # ExxonMobil
        "CVX",     # Chevron
        "COP",     # ConocoPhillips
        "OXY",     # Buffett pick
    ],

    "גרעין": [
        # --- Original ---
        "CEG",     # Constellation Energy
        "VST",     # Vistra
        "GEV",     # GE Vernova
        "OKLO",    # Oklo SMR
        "SMR",     # NuScale
        "CCJ",     # Cameco — Uranium
        "SO",      # Southern Company
        "BEP",     # Brookfield Renewable
        # --- Nuclear additions ---
        "NNE",     # Nano Nuclear Energy
        "DUK",     # Duke Energy
        "NRG",     # NRG Energy
        "URA",     # Global X Uranium ETF
        "URNM",    # Sprott Uranium Miners ETF
        "NLR",     # VanEck Uranium+Nuclear ETF
        "NUKZ",    # Range Nuclear Renaissance ETF
        "CEZ",     # CEZ Group
        "LEU",     # Centrus Energy
        "FCU",     # Fission Uranium
        "EU",      # enCore Energy
        "NXE",     # NexGen Energy
        "UEC",     # Uranium Energy Corp
        "UUUU",    # Energy Fuels
        "DNN",     # Denison Mines
        "YCA",     # Yellow Cake
        "NEE",     # NextEra Energy
        "PEG",     # PSEG
        "ELE",     # Endesa
        "KAP",     # Kazatomprom
        "PCG",     # PG&E
        "BOE",     # Brookfield Renewable Partners
        "SLX",     # VanEck Steel ETF
        "DYL",     # Deep Yellow
        "PDN",     # Paladin Energy
    ],

    "אנרגיה ירוקה": [
        "BE",      # Bloom Energy
        "ENPH",    # Enphase
        "FSLR",    # First Solar
        "NEE",     # NextEra Energy
    ],

    "פיננסים": [
        # --- Original ---
        "JPM",     # JPMorgan
        "GS",      # Goldman Sachs
        "MSTR",    # MicroStrategy — Bitcoin play
        # --- KRE: Small/Community Banks ---
        "CBU",     "PFS",     "HTH",     "NBHC",    "NIC",
        "FFBC",    "CCB",     "TOWN",    "FRME",    "LOB",
        "PRK",     "HIFS",    "HOPE",    "NBTB",    "SYBT",
        "LKFN",    "WABC",    "CNOB",    "STBA",    "OCFC",
        "BY",      "OBK",     "AMAL",    "FSUN",    "PEBO",
        "FFWM",    "HFWA",    "ORRF",    "SBSI",    "HTBK",
        "WASH",
        # --- KRE: Mid-Size Banks ---
        "ABCB",    "ASB",     "AX",      "BFC",     "BHRB",
        "BKU",     "BOKF",    "BPOP",    "BUSE",    "CATY",
        "CFFN",    "CFR",     "CPF",     "CTBI",    "CUBI",
        "DCOM",    "EBC",     "EFSC",    "EGBN",    "EQBK",
        "ESQ",     "FBK",     "FBNC",    "FCF",     "FFIC",
        "FISI",    "GSBC",    "HAFC",    "HWC",     "IBCP",
        "KRNY",    "MBWM",    "MOFG",    "MPB",     "MSBI",
        "NBBK",    "NBN",     "NFBK",    "ONB",     "OSBC",
        "PFBC",    "QCRH",    "SHBI",    "SMBC",    "SRCE",
        "STEL",    "TCBK",    "TCBX",    "TFIN",    "THFF",
        "TMP",     "TRMK",    "TRST",    "UMBF",    "UVSP",
        "VLY",     "WSFS",
        # --- KRE: Larger Regional Banks ---
        "AMTB",    "AUB",     "BANC",    "BANF",    "BANR",
        "BBT",     "BOH",     "CAC",     "CADE",    "CASH",
        "CBSH",    "CFG",     "CHCO",    "CLBK",    "COLB",
        "CVBF",    "EWBC",    "FBP",     "FFIN",    "FHB",
        "FHN",     "FIBK",    "FLG",     "FNB",     "FULT",
        "GBCI",    "HBAN",    "HBNC",    "HOMB",    "IBOC",
        "INDB",    "MCB",     "MTB",     "NRIM",    "NWBI",
        "OFG",     "OZK",     "PB",      "PGC",     "PNFP",
        "RF",      "RNST",    "SBCF",    "SFBS",    "SFNC",
        "SSB",     "TBBK",    "TCBI",    "TFC",     "TFSL",
        "UBSI",    "UCB",     "WAFD",    "WAL",     "WBS",
        "WSBC",    "WTFC",    "ZION",
    ],

    "חומרי גלם": [
        # --- Rare Earth / Critical Minerals ---
        "USAR",    # USA Rare Earth
        "CRML",    # Critical Metals
        "UAMY",    # US Antimony
        "NB",      # NioCorp Developments
        "TMC",     # The Metals Company
        "AREC",    # American Rare Earths
        "MP",      # MP Materials
        "IDR",     # Idaho Strategic Resources
        "UUUU",    # Energy Fuels (also in גרעין)
        "NAK",     # Northern Dynasty Minerals
        "REMX",    # VanEck Rare Earth ETF
        "IE",      # Ivanhoe Electric
        "TMQ",     # Trilogy Metals
        # --- Copper & Base Metals ---
        "HBM",     # Hudbay Minerals
        "SCCO",    # Southern Copper
        "ERO",     # Ero Copper
        "TGB",     # Taseko Mines
        "COPX",    # Global X Copper Miners ETF
        "FCX",     # Freeport-McMoRan
        "CPER",    # US Copper Index Fund
    ],

    # ⚠️ HIGH VOLATILITY — SPECULATIVE (added 2026-07-14). Pure-play quantum
    # computing: routinely ±10-20% in a session without news, P/S > 100,
    # ongoing operating losses, ATR% ~9-10 (vs 1.5-8 for the rest of the
    # watchlist). Deliberately NOT special-cased anywhere: eligibility's
    # volatility component already penalizes ATR% > 8, and all stop sizing
    # is ATR-proportional so stops scale with their volatility naturally.
    # Read alerts on these symbols with that context in mind.
    "Quantum Computing": [
        "IONQ",    # IonQ — trapped-ion, largest pure-play by revenue
        "RGTI",    # Rigetti Computing — superconducting qubits
        "QBTS",    # D-Wave Quantum — quantum annealing
    ],
}

# ─────────────────────────────────────────────
#  הגדרות התראות
# ─────────────────────────────────────────────

# ציון מינימלי לשליחת התראה (swing trading — BUY ומעלה)
ALERT_MIN_SCORE: int = 65

# מדיניות cooldown (D3): התראה אחת לכל מניה ליום (UTC) — קבוע במסד הנתונים
# (db.was_alerted_today), לא ניתן להגדרה.

# רק verdict אלה יגרמו לשליחת התראה
ALERT_VERDICTS: list[str] = ["BUY", "STRONG BUY"]

# שינוי מחיר מינימלי (חיובי בלבד — רק BUY)
ALERT_MIN_PRICE_CHANGE: float = 0.5

# ─────────────────────────────────────────────
#  ספי RSI — מוזנים ל-analyzers/technical.py
#  (הגדרה יחידה; לולאת ההתראות צורכת רק את
#   triggered_signals של full_analysis)
# ─────────────────────────────────────────────
# מחוץ לטווח הווטו — פסילה מוחלטת (score 0, NEUTRAL)
RSI_VETO_MIN: int = 35
RSI_VETO_MAX: int = 75

# הטווח הבריא לסווינג — מזכה ב"rsi_healthy_range" (תנאי חובה להתראה)
RSI_HEALTHY_MIN: int = 45
RSI_HEALTHY_MAX: int = 65

# אישור מומנטום — נר אחרון חייב להיות ירוק (close > open)
ALERT_REQUIRE_GREEN_CANDLE: bool = True

# ─────────────────────────────────────────────
#  Composite scoring engine (analyzers/composite.py)
#  Parallel to the legacy gate path — NOT wired into alerting yet.
# ─────────────────────────────────────────────
# Lookback window (completed trading days) for the Relative Strength ratio
# RS = (1 + stock_return) / (1 + SPY_return)
COMPOSITE_RS_WINDOW_DAYS: int = int(os.getenv("COMPOSITE_RS_WINDOW_DAYS", "60"))

# Regime-dependent thresholds (regime = SPY close vs SPY SMA150, once per cycle)
COMPOSITE_RS_REQUIRED_BULL: float = float(os.getenv("COMPOSITE_RS_REQUIRED_BULL", "1.0"))
COMPOSITE_RS_REQUIRED_BEAR: float = float(os.getenv("COMPOSITE_RS_REQUIRED_BEAR", "1.2"))
COMPOSITE_REQUIRED_SCORE_BULL: int = int(os.getenv("COMPOSITE_REQUIRED_SCORE_BULL", "70"))
COMPOSITE_REQUIRED_SCORE_BEAR: int = int(os.getenv("COMPOSITE_REQUIRED_SCORE_BEAR", "75"))

# Relative volume at which the volume component reaches full points
# (session-elapsed normalized — see composite.py _session_fraction)
COMPOSITE_RELVOL_FULL: float = float(os.getenv("COMPOSITE_RELVOL_FULL", "1.5"))

# Relative volume required for the SMA150-reclaim breakout bonus (completed bar)
COMPOSITE_BREAKOUT_RELVOL: float = float(os.getenv("COMPOSITE_BREAKOUT_RELVOL", "2.0"))

# Extension above SMA150 (%) that still earns full extension points;
# points taper linearly to 0 at twice this value
COMPOSITE_EXTENSION_MAX_PCT: float = float(os.getenv("COMPOSITE_EXTENSION_MAX_PCT", "10.0"))

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

# כמה פעמים בשעה לבדוק (כל 15 דק' = 4 פעמים)
CHECK_INTERVAL_MINUTES: int = 15

# ─────────────────────────────────────────────
#  הגדרות גרף
# ─────────────────────────────────────────────
CHART_WIDTH:  int = 1200
CHART_HEIGHT: int = 800
CHART_SCALE:  int = 2
CHART_THEME:  str = "#0d1117"

# ─────────────────────────────────────────────
#  קטגוריות — לשימוש הוספת מניה מהבוט
#  /add AAPL "מגה טק"
# ─────────────────────────────────────────────
CATEGORIES: list[str] = list(WATCHLIST.keys())

# ─────────────────────────────────────────────
#  Watchlist Architecture
# ─────────────────────────────────────────────
# Maximum number of symbols in the Active tier at one time
ACTIVE_MAX_SIZE: int = int(os.getenv("ACTIVE_MAX_SIZE", "30"))

# Maximum number of bank/financial sector symbols allowed in Active
ACTIVE_BANK_MAX: int = int(os.getenv("ACTIVE_BANK_MAX", "8"))

# Minimum 3-month average daily share volume for eligibility
ELIGIBILITY_MIN_AVG_VOLUME: int = int(os.getenv("ELIGIBILITY_MIN_AVG_VOLUME", "250000"))

# Minimum average daily dollar volume (price × avg_volume) for eligibility
ELIGIBILITY_MIN_DOLLAR_VOL: int = int(os.getenv("ELIGIBILITY_MIN_DOLLAR_VOL", "10000000"))

# Minimum price for eligibility (avoids penny stocks)
ELIGIBILITY_MIN_PRICE: float = float(os.getenv("ELIGIBILITY_MIN_PRICE", "3.0"))

# Number of trading days used to compute average dollar volume (lookback)
ELIGIBILITY_LOOKBACK_DAYS: int = int(os.getenv("ELIGIBILITY_LOOKBACK_DAYS", "63"))

# Number of calendar days before last bar is considered stale
ELIGIBILITY_STALE_DAYS: int = int(os.getenv("ELIGIBILITY_STALE_DAYS", "3"))

# Minimum relevance score to qualify for ACTIVE promotion
PROMOTION_THRESHOLD: int = int(os.getenv("PROMOTION_THRESHOLD", "60"))

# Consecutive evaluations at or above PROMOTION_THRESHOLD required to promote
PROMOTION_CONSEC_REQUIRED: int = int(os.getenv("PROMOTION_CONSEC_REQUIRED", "2"))

# Relevance score below which demotion is evaluated
DEMOTION_THRESHOLD: int = int(os.getenv("DEMOTION_THRESHOLD", "45"))

# Consecutive evaluations below DEMOTION_THRESHOLD required to demote
DEMOTION_CONSEC_REQUIRED: int = int(os.getenv("DEMOTION_CONSEC_REQUIRED", "2"))

# Minimum trading days a symbol must stay in ACTIVE before demotion is eligible
DWELL_MIN_DAYS: int = int(os.getenv("DWELL_MIN_DAYS", "5"))

# Score advantage a Monitor candidate needs over lowest Active symbol to replace it
REPLACEMENT_MARGIN: int = int(os.getenv("REPLACEMENT_MARGIN", "5"))

# Whether to send BUY/SELL alerts for ETF and index symbols
ETF_ALERTS_ENABLED: bool = os.getenv("ETF_ALERTS_ENABLED", "false").lower() == "true"

# Categories considered to be bank/financial sector (used for bank-cap enforcement)
BANK_CATEGORIES: frozenset[str] = frozenset(["פיננסים"])

# ─────────────────────────────────────────────
#  Market Data Validation (Phase 3 — data/market_data_validator.py)
# ─────────────────────────────────────────────
# Minimum number of completed daily candles required to evaluate a symbol
MARKET_DATA_MIN_HISTORY_DAYS: int = int(os.getenv("MARKET_DATA_MIN_HISTORY_DAYS", "30"))

# yfinance period string used when fetching daily history for validation
# (must comfortably cover ELIGIBILITY_LOOKBACK_DAYS plus a safety buffer)
MARKET_DATA_HISTORY_PERIOD: str = os.getenv("MARKET_DATA_HISTORY_PERIOD", "6mo")

# Bounded retries for transient yfinance failures (not for invalid symbols)
MARKET_DATA_MAX_RETRIES: int = int(os.getenv("MARKET_DATA_MAX_RETRIES", "2"))

# Base backoff seconds; actual wait is base * (2 ** attempt), capped
MARKET_DATA_RETRY_BACKOFF_SECONDS: float = float(os.getenv("MARKET_DATA_RETRY_BACKOFF_SECONDS", "1.5"))

# Max symbols per yfinance batch download call
MARKET_DATA_BATCH_SIZE: int = int(os.getenv("MARKET_DATA_BATCH_SIZE", "50"))

# How long to wait before retrying a symbol that hit a transient provider error
MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS: int = int(os.getenv("MARKET_DATA_PROVIDER_ERROR_RETRY_HOURS", "4"))

# How long to wait before retrying a symbol classified as invalid/unsupported
# (long, to avoid hammering a permanently broken ticker on every daily run)
MARKET_DATA_INVALID_SYMBOL_RETRY_HOURS: int = int(os.getenv("MARKET_DATA_INVALID_SYMBOL_RETRY_HOURS", "168"))

# How long to wait before re-checking a symbol with a data-quality problem
# (stale data, insufficient history, missing volume, etc.)
MARKET_DATA_DATA_QUALITY_RETRY_HOURS: int = int(os.getenv("MARKET_DATA_DATA_QUALITY_RETRY_HOURS", "24"))

# ─────────────────────────────────────────────
#  Dry-run watchlist evaluator (Phase 4 — services/watchlist_evaluator.py)
# ─────────────────────────────────────────────
# Fraction of evaluated symbols returning RATE_LIMITED/PROVIDER_ERROR/
# TEMPORARY_FAILURE above which a run is treated as "provider degraded"
# (suppresses mass ACTIVE -> MONITOR demotion proposals for that run).
WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT: float = float(
    os.getenv("WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT", "0.4")
)

# ─────────────────────────────────────────────
#  Watchlist evaluation scheduler (Phase 6 — services/watchlist_scheduler.py)
# ─────────────────────────────────────────────
# Default daily evaluation time, America/New_York. 17:30 is safely after
# both the regular 16:00 close and any early close (~13:00).
WATCHLIST_SCHEDULE_HOUR_ET: int = int(os.getenv("WATCHLIST_SCHEDULE_HOUR_ET", "17"))
WATCHLIST_SCHEDULE_MINUTE_ET: int = int(os.getenv("WATCHLIST_SCHEDULE_MINUTE_ET", "30"))

# Automatic scheduled runs are dry-run unless this is explicitly true.
# Has no effect on manual/CLI-forced apply calls (those are an explicit
# human override regardless of this setting).
WATCHLIST_SCHEDULE_APPLY: bool = os.getenv("WATCHLIST_SCHEDULE_APPLY", "false").lower() == "true"

# Minutes after which a still-'started' evaluation run is treated as a
# crashed/stuck process rather than a genuinely active one.
WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES: int = int(
    os.getenv("WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES", "60")
)

# Extra one-off US market closure dates not covered by the built-in
# holiday approximation (e.g. an unscheduled closure), as comma-separated
# ISO dates: "2026-01-09,2027-04-12". Empty by default.
WATCHLIST_EXTRA_HOLIDAY_DATES: frozenset[str] = frozenset(
    d.strip() for d in os.getenv("WATCHLIST_EXTRA_HOLIDAY_DATES", "").split(",") if d.strip()
)

# ─────────────────────────────────────────────
#  Telegram watchlist refresh commands (Phase 7 — bot/telegram_bot.py)
# ─────────────────────────────────────────────
# /refresh_watchlist apply is rejected outright unless this is true. Even
# when true, the command additionally requires the literal word "confirm"
# (e.g. "/refresh_watchlist apply confirm") — this flag alone is not enough
# to let a bare "/refresh_watchlist apply" write anything.
TELEGRAM_ALLOW_WATCHLIST_APPLY: bool = os.getenv("TELEGRAM_ALLOW_WATCHLIST_APPLY", "false").lower() == "true"

# Default/maximum number of symbols listed per change-type bucket in
# /watchlist_changes before truncating with "...and N more".
WATCHLIST_CHANGES_DEFAULT_LIMIT: int = int(os.getenv("WATCHLIST_CHANGES_DEFAULT_LIMIT", "20"))
WATCHLIST_CHANGES_MAX_LIMIT: int = int(os.getenv("WATCHLIST_CHANGES_MAX_LIMIT", "100"))

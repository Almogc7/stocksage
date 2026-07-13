# 📈 StockSage

> Automated swing-trading alert system for US stocks.
> Monitors 80+ symbols, runs technical analysis every 15 minutes,
> and sends buy alerts with chart images directly to Telegram.

---

## 🚀 Features

- **9-gate alert filter** — only high-quality BUY signals get through
- **Composite buy score 0–100** across 7 weighted indicators
- **Chart image attached to every alert** (candlesticks, MA150, MA200, RSI, Volume)
- **Morning scan** every weekday at market open — top 5 opportunities
- **80+ symbols** across 13 categories (AI, Crypto, Nuclear, Space, Data Center, and more)
- **Hebrew/English bilingual** Telegram bot (`/language he` / `/language en`)
- **Trade journal** with P&L tracking via `/trade` commands
- **Streamlit dashboard** with live prices and interactive charts
- **SQLite database** — no external DB required

---

## 🛠️ Tech Stack

| Library | Purpose |
|---|---|
| `python-telegram-bot` | Async Telegram bot framework (v21+) |
| `yfinance` | Live and historical stock price data |
| `pandas` | OHLCV data manipulation and analysis |
| `numpy` | Numerical computations |
| `ta` | Technical analysis indicators (RSI, MACD, Bollinger Bands, etc.) |
| `streamlit` | Interactive web dashboard |
| `plotly` | Interactive charting for the dashboard |
| `kaleido` | Static chart image export for Telegram alerts |
| `schedule` | Cron-style background job scheduler |
| `sqlalchemy` | SQLite ORM for trade/alert persistence |
| `python-dotenv` | Environment variable loading from `.env` |
| `anthropic` | Claude AI SDK (reserved for future AI features) |
| `aiohttp` | Async HTTP client |
| `requests` | Synchronous HTTP requests |
| `textblob` | NLP/sentiment analysis (stub) |
| `newsapi-python` | News feed integration (stub) |

---

## 📁 Project Structure

```
stocksage/
├── main.py                   # Entry point — starts bot + background agent
├── config.py                 # All configuration: thresholds, watchlist, market hours
├── dashboard.py              # Streamlit web dashboard
├── requirements.txt          # Python dependencies
├── test_fetch.py             # Integration smoke test for data pipeline
│
├── agent/
│   ├── core.py               # Background scheduler: price checks, morning scan, alert dispatch
│   ├── decision_engine.py    # Stub — reserved for AI decision logic
│   └── watchlist.py          # Stub — reserved for dynamic watchlist management
│
├── analyzers/
│   ├── technical.py          # Full technical analysis engine + composite scoring
│   ├── chart_generator.py    # Generates candlestick chart images for Telegram
│   ├── price_alerts.py       # Stub — reserved for price-movement alert logic
│   └── sentiment.py          # Stub — reserved for news sentiment analysis
│
├── bot/
│   ├── telegram_bot.py       # All Telegram command handlers and bilingual strings
│   └── formatters.py         # Stub — reserved for message formatting helpers
│
├── data/
│   ├── fetcher.py            # yfinance wrappers: current price, OHLCV history, market hours
│   └── news_fetcher.py       # Stub — reserved for news API integration
│
└── db/
    ├── database.py           # SQLite schema, CRUD operations for watchlist/trades/alerts
    ├── models.py             # Stub — reserved for ORM model definitions
    └── stocksage.db          # SQLite database file (auto-created on first run)
```

---

## ⚙️ Installation

### Prerequisites

- Python 3.11+
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Git

### Setup

**1. Clone the repository**

```bash
git clone https://github.com/Almogc7/StockSage_V2.git
cd StockSage_V2
```

**2. Create a virtual environment**

```bash
python -m venv stocksage/venv
# Windows
stocksage\venv\Scripts\activate
# macOS / Linux
source stocksage/venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r stocksage/requirements.txt
```

**4. Create the `.env` file**

```bash
# Create stocksage/.env with the following content:
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

**5. Run the application**

```bash
# Terminal 1 — main bot + background agent
python stocksage/main.py

# Terminal 2 — Streamlit dashboard (optional)
streamlit run stocksage/dashboard.py
```

---

## 🔐 Environment Variables

| Variable | Description | Required |
|---|---|---|
| `TELEGRAM_TOKEN` | Bot token from @BotFather | ✅ Yes |
| `TELEGRAM_CHAT_ID` | Your Telegram chat/user ID for alert delivery | ✅ Yes |
| `AUTHORIZED_CHAT_IDS` | Comma-separated chat IDs allowed to issue bot commands (falls back to `TELEGRAM_CHAT_ID` if unset) | Optional |

### Watchlist lifecycle variables (safe defaults — leave unset initially)

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_ALLOW_WATCHLIST_APPLY` | Allow `/refresh_watchlist apply confirm` to write real changes from Telegram | `false` |
| `WATCHLIST_SCHEDULE_APPLY` | Allow an unattended scheduled run to apply real changes (no automatic scheduler runs yet regardless) | `false` |
| `WATCHLIST_SCHEDULE_HOUR_ET` / `WATCHLIST_SCHEDULE_MINUTE_ET` | Daily evaluation time, America/New_York | `17` / `30` |
| `WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES` | Minutes before a stuck `started` run is auto-marked failed | `60` |
| `WATCHLIST_EXTRA_HOLIDAY_DATES` | Extra comma-separated US market closure dates (`YYYY-MM-DD`) beyond the built-in holiday calendar | empty |
| `WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT` | Fraction of transient yfinance failures that triggers provider-degraded handling | `0.4` |
| `ACTIVE_MAX_SIZE` / `ACTIVE_BANK_MAX` | ACTIVE-tier symbol cap / bank-sector sub-cap | `30` / `8` |
| `PROMOTION_THRESHOLD` / `DEMOTION_THRESHOLD` | Relevance-score thresholds for tier transitions | `60` / `45` |

See `CLAUDE_CHANGES.md` and `STOCKSAGE_FINAL_ROLLOUT_PLAN.md` for the full list and recommended rollout order — **leave `TELEGRAM_ALLOW_WATCHLIST_APPLY` and `WATCHLIST_SCHEDULE_APPLY` unset (false) until you've reviewed several days of dry-run output.**

---

## 🤖 Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome message and quick-start guide |
| `/analyze <SYMBOL>` | Run full technical analysis on a symbol (e.g. `/analyze NVDA`) |
| `/scan` | Trigger a manual morning scan — returns top 5 scoring stocks |
| `/watchlist` | Show all tracked symbols with live prices |
| `/add <SYMBOL> <CATEGORY>` | Add a symbol to the watchlist (e.g. `/add AAPL "מגה טק"`) |
| `/remove <SYMBOL>` | Remove a symbol from the watchlist |
| `/trade <BUY\|SELL> <SYMBOL> <qty> <price>` | Log a trade to the journal |
| `/trades` | List all logged trades |
| `/summary <SYMBOL>` | Show P&L summary for a symbol |
| `/alerts` | Show all alerts sent today |
| `/status` | Show bot status, market hours, and next check time |
| `/language <he\|en>` | Switch bot language (Hebrew / English) |
| `/test` | Send a test alert to confirm the bot is working |
| `/help` | Show all available commands |

### Watchlist lifecycle commands

These require authorization like every other command above. The watchlist
itself is multi-tier (ACTIVE / MONITOR / ETF_INDEX_CONTEXT /
TEMPORARILY_INELIGIBLE / USER_REMOVED), persisted in SQLite, and evaluated
by a dry-run/apply engine — see `CLAUDE_CHANGES.md` and
`STOCKSAGE_FINAL_ROLLOUT_PLAN.md` for the full design.

| Command | Description |
|---|---|
| `/watchlist_active` | List ACTIVE-tier symbols (the ones actually scanned for alerts) with their relevance score |
| `/watchlist_monitor` | List MONITOR-tier symbols (tracked, not yet scanned) |
| `/watchlist_context` | List ETF/index/crypto symbols (price context only, never scanned for BUY alerts) |
| `/watchlist_ineligible` | List TEMPORARILY_INELIGIBLE symbols and why |
| `/watchlist_status <SYMBOL>` | Full tier/score/streak detail for one symbol |
| `/refresh_watchlist` | Run a watchlist eligibility evaluation — **dry-run by default, never changes any state** |
| `/refresh_watchlist apply confirm` | Apply real changes — **disabled unless `TELEGRAM_ALLOW_WATCHLIST_APPLY=true`**, and still requires the literal word `confirm` |
| `/watchlist_refresh_status` | Show the most recent evaluation run's status/counts |
| `/watchlist_changes [N] [run ID]` | Show promotions/demotions/recoveries/newly-ineligible from the latest (or a specific) run |

**Safety notes:**
- `/refresh_watchlist` with no arguments, or `/refresh_watchlist dry_run`, is always safe — it never writes to the watchlist.
- `/refresh_watchlist apply` (without `confirm`, or with the config flag off) is refused with a clear message — it never silently applies.
- Rollback of an applied run is **CLI-only**, by design — see `scripts/rollback_evaluation_run.py` in the rollout plan. There is no Telegram rollback command.

---

## 🚨 Alert System — 9 Gates

An alert is only sent when **all** of the following conditions are met:

1. **Market is open** — checks US market hours (9:30–16:00 ET); controlled by `MARKET_OPEN_HOUR` / `MARKET_CLOSE_HOUR` in `config.py`
2. **Score threshold** — composite buy score must be ≥ 65; controlled by `ALERT_MIN_SCORE`
3. **Verdict filter** — verdict must be `BUY` or `STRONG BUY`; controlled by `ALERT_VERDICTS`
4. **Price above EMA150** — hard veto: price must be above the 150-period EMA (trend filter)
5. **RSI range** — RSI must be between 45 and 68 (not overbought, not oversold); controlled by `ALERT_RSI_MIN` / `ALERT_RSI_MAX`
6. **Minimum price change** — intraday move must be at least +0.5%; controlled by `ALERT_MIN_PRICE_CHANGE`
7. **Green candle confirmation** — the most recent candle must close above its open; controlled by `ALERT_REQUIRE_GREEN_CANDLE`
8. **Cooldown guard** — no alert for the same symbol within the last 2 hours; controlled by `ALERT_COOLDOWN_HOURS`
9. **Daily dedup** — in-memory session guard prevents duplicate alerts within the same process run

---

## 📊 Scoring System

The composite buy score (0–100) is built from 7 weighted components:

| Component | Points | Condition |
|---|---|---|
| Price above EMA150 | +20 | Always awarded if the veto gates pass |
| EMA150 > EMA200 | +15 | Long-term uptrend confirmed |
| MACD bullish crossover | +20 | Bullish crossover within the last 3 candles |
| RSI in healthy zone | +15 | RSI between 45–65 (ideal swing range); +5 for fringe zone 35–45 / 65–75 |
| Volume spike | +15 | Current volume > 1.5× the 20-day average |
| Stochastic RSI crossover | +10 | %K crossed above %D from below 0.3 |
| Price above VWAP | +5 | Price above rolling 20-period VWAP |

**Verdict thresholds:**

| Score | Verdict |
|---|---|
| 75–100 | 🟢 STRONG BUY |
| 55–74 | 🔵 BUY |
| 35–54 | 🟡 WATCH |
| 0–34 | ⚪ NEUTRAL |

---

## 🗂️ Watchlist Categories

| Category | Symbols |
|---|---|
| מדדים (Indices) | ^GSPC, ^IXIC, QQQ, ^DJI, ^RUT, ^VIX |
| קריפטו (Crypto) | BTC-USD, ETH-USD |
| ETFs | SPY, VOO, QQQ, VGT, XLK, SOXX, CIBR, ARKK, SCHG, UFO, NUKZ |
| AI & Semiconductors | NVDA, AMD, INTC, AVGO, ARM, QCOM, MRVL, ANET |
| מגה טק (Mega Tech) | GOOGL, AMZN, MSFT, META, AAPL, TSLA, CBRS |
| ענן ותוכנה (Cloud & Software) | SNOW, PLTR, DDOG, NET, CRM |
| סייבר (Cybersecurity) | CRWD, PANW, FTNT, ZS, S |
| תשתיות Data Center | EQIX, DLR, IRM, VRT, APLD, DOCN, GLW, ETN, MOD, CSCO |
| חלל (Space) | RKLB, ASTS, LUNR, LMT, LHX, PL, BA, NOC |
| אנרגיה (Energy) | XOM, CVX, COP, OXY |
| גרעין (Nuclear) | CEG, VST, GEV, OKLO, SMR, CCJ, SO, BEP |
| אנרגיה ירוקה (Green Energy) | BE, ENPH, FSLR, NEE |
| פיננסים (Financials) | JPM, GS, MSTR |

---

## 🖥️ Running on Windows (24/7)

To keep StockSage running in the background after you close your terminal, use a `.vbs` launcher script:

**1. Create `start_stocksage.vbs`** in the project root:

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d C:\path\to\StockSage_V2 && stocksage\venv\Scripts\python.exe stocksage\main.py >> stocksage\log.txt 2>&1", 0, False
```

**2. Run it silently** by double-clicking the `.vbs` file — no console window will appear.

**3. Auto-start on login** — press `Win + R`, type `shell:startup`, and copy the `.vbs` file into the Startup folder.

**4. Check the log** at `stocksage/log.txt` to monitor activity and debug issues.

---

## 📝 License

MIT License — see [LICENSE](../LICENSE) for details.

---

## ⚠️ Disclaimer

StockSage is built for **educational and research purposes only**. It is not financial advice, and nothing in this project should be construed as a recommendation to buy or sell any security. Trading stocks involves significant risk. Past performance of any signal, score, or indicator does not guarantee future results. Always do your own research and consult a qualified financial advisor before making investment decisions.


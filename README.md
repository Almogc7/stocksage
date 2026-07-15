# 📈 StockSage

> Automated swing-trading alert system for US stocks.
> Tracks 400+ symbols across 15 categories; a nightly eligibility engine
> promotes up to 30 into the ACTIVE tier, which is scanned every 15 minutes
> for buy alerts sent to Telegram with chart images.

---

## 🚀 Features

- **7-gate alert filter** — only high-quality BUY signals get through
- **Legacy opportunity score 0–100** across 7 weighted indicators (drives alerts)
- **Parallel composite engine + advisory position management** (`/composite`, `/position` — observation mode)
- **Chart image attached to every alert** (candlesticks, SMA150, SMA200, RSI, Volume)
- **Morning scan** every weekday at market open — top 5 opportunities
- **400+ symbols** across 15 categories (AI, Crypto, Nuclear, Space, Data Center, Quantum, and more) with a multi-tier lifecycle: nightly eligibility evaluation promotes up to 30 into the alert-scanned ACTIVE tier
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
git clone https://github.com/Almogc7/stocksage.git
cd stocksage
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
| `WATCHLIST_SCHEDULE_APPLY` | Global default for unattended scheduled runs. **Note:** the nightly Windows task (02:30 IL, see "Running on Windows" below) passes an explicit `--scheduled-apply` CLI override, so it applies real changes every market night regardless of this default — apply intent deliberately lives in the task, not in a global flag | `false` |
| `HEALTHCHECK_PING_URL` | Dead-man's-switch URL pinged after every successful agent check cycle (empty = disabled) | empty |
| `WATCHLIST_SCHEDULE_HOUR_ET` / `WATCHLIST_SCHEDULE_MINUTE_ET` | Daily evaluation time, America/New_York | `17` / `30` |
| `WATCHLIST_SCHEDULE_STUCK_RUN_TIMEOUT_MINUTES` | Minutes before a stuck `started` run is auto-marked failed | `60` |
| `WATCHLIST_EXTRA_HOLIDAY_DATES` | Extra comma-separated US market closure dates (`YYYY-MM-DD`) beyond the built-in holiday calendar | empty |
| `WATCHLIST_PROVIDER_OUTAGE_THRESHOLD_PCT` | Fraction of transient yfinance failures that triggers provider-degraded handling | `0.4` |
| `ACTIVE_MAX_SIZE` / `ACTIVE_BANK_MAX` | ACTIVE-tier symbol cap / bank-sector sub-cap | `30` / `8` |
| `PROMOTION_THRESHOLD` / `DEMOTION_THRESHOLD` | Relevance-score thresholds for tier transitions | `60` / `45` |

Binding architecture rulings live in `docs/DECISIONS.md` — **leave `TELEGRAM_ALLOW_WATCHLIST_APPLY` and `WATCHLIST_SCHEDULE_APPLY` unset (false); the nightly task carries its own explicit apply intent.**

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
| `/admin_help` | Technical/operational command menu (`/test`, refresh auditing) |
| `/composite <SYMBOL>` | Composite-engine score breakdown — observation mode, English-only, separate scale from the legacy score |
| `/position <SYMBOL> <ENTRY> <DATE> [STOP]` | Advisory trailing-stop / exit-signal check for an open LONG position |
| `/pin <SYMBOL>` | Pin a stock permanently into the ACTIVE tier (📌 marker; never demoted/evicted by the nightly evaluation; alerting unchanged) |
| `/unpin <SYMBOL>` | Release a pin — normal lifecycle resumes with fresh hysteresis counters |

Aliases: `/watchlist_add` = `/add`, `/watchlist_remove` = `/remove`, `/morning_scan` = `/scan`.

### Watchlist lifecycle commands

These require authorization like every other command above. The watchlist
itself is multi-tier (ACTIVE / MONITOR / ETF_INDEX_CONTEXT /
TEMPORARILY_INELIGIBLE / USER_REMOVED), persisted in SQLite, and evaluated
by a dry-run/apply engine — see `docs/DECISIONS.md` for the binding
architecture rulings and `CLAUDE.md` for the full stack map.

| Command | Description |
|---|---|
| `/watchlist_active` | List ACTIVE-tier symbols (the ones actually scanned for alerts) with their relevance score |
| `/watchlist_monitor [N]` | List top-N scored MONITOR-tier symbols (tracked, not yet scanned; default 10, max 100) |
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

## 🚨 Alert System — 7 Gates

The 15-minute check runs only while the US market is open (9:30–16:00 ET),
and scans the ACTIVE tier only. For each symbol, an alert fires only when
**all seven** gates pass — all technical judgment comes from
`full_analysis()`; the alert loop adds no indicator checks of its own:

1. **Price move** — intraday change ≥ +0.5%; controlled by `ALERT_MIN_PRICE_CHANGE`
2. **Session dedup** — not already alerted in this process run (in-memory guard)
3. **Daily cooldown** — at most one alert per symbol per UTC day, DB-backed (deliberately not configurable)
4. **Data fetch** — a valid 1-year history fetch must succeed
5. **Score & verdict** — legacy opportunity score ≥ 65 (`ALERT_MIN_SCORE`) and verdict `BUY` / `STRONG BUY` (`ALERT_VERDICTS`)
6. **Required signals** — both `rsi_healthy_range` (RSI 45–65; `RSI_HEALTHY_MIN`/`RSI_HEALTHY_MAX`) and `volume_spike` must be triggered
7. **Green candle confirmation** — the last completed candle closed above its open; controlled by `ALERT_REQUIRE_GREEN_CANDLE`

Inside `full_analysis()` itself, the score is hard-vetoed to 0 unless price
is above the **SMA150** and RSI is inside the 35–75 veto band
(`RSI_VETO_MIN`/`RSI_VETO_MAX`).

---

## 📊 Legacy Opportunity Score (drives alerting)

The legacy opportunity score (0–100) is built from 7 weighted components.
(Not to be confused with the separate composite engine below — the two use
different scales and different BUY bars.)

| Component | Points | Condition |
|---|---|---|
| Price above SMA150 | +20 | Always awarded if the veto gates pass |
| SMA150 > SMA200 | +15 | Long-term uptrend confirmed |
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

## 🧪 Composite Engine & Position Management (advisory — not wired into alerts)

`analyzers/composite.py` — a parallel scoring engine run alongside the
legacy score for comparison, **not** for alerting: 4 layers × 25 points
(trend/extension, momentum, volume, relative strength vs SPY) behind a
two-part SMA hard gate, with a once-per-cycle SPY regime modifier (bull/bear
adjusts the required RS ratio 1.0/1.2 and the BUY-flag bar 70/75) and
score-keyed ATR stop sizing (2.0–3.0×). Computes on completed bars only.
**Composite scores are not comparable to the legacy 0–100 score.**

`analyzers/position_management.py` — advisory trailing-stop / exit module
for open LONG positions, built on the composite engine's stop sizing:
staged policy (<1R initial stop, ≥1R breakeven, >1.5R Chandelier 3.0× ATR,
tightened to 2.5× when exit signals fire), monotonically non-decreasing
stops, three advisory exit flags (bearish RSI divergence, climax volume,
RSI ≥ 80), and a partial-exit suggestion at ≥2R.

Surfaced read-only via CLI (`scripts/run_composite_scan.py`,
`scripts/run_position_check.py`) and the `/composite` and `/position` bot
commands. Nothing here executes trades or changes alerting behavior.

---

## 🗂️ Watchlist Categories

15 categories: indices (מדדים), crypto (קריפטו), ETFs, AI & Semiconductors,
mega tech (מגה טק), cloud & software (ענן ותוכנה), cybersecurity (סייבר),
data-center infrastructure, space & defense (חלל), energy (אנרגיה), nuclear
(גרעין), green energy (אנרגיה ירוקה), financials (פיננסים), raw materials
(חומרי גלם), and Quantum Computing (flagged **high volatility —
speculative**; see the note in `config.py`).

Membership is deliberately **not** listed here — after first-run seeding
from `config.py`'s `WATCHLIST` dict, **the SQLite database is the source of
truth** and membership changes nightly. See the live state with
`/watchlist` (per-category summary), `/watchlist_active`, and
`/watchlist_monitor`.

---

## 🖥️ Running on Windows (24/7)

Deployment is supervised by **Windows Task Scheduler** — one-time setup from
an **elevated** PowerShell (it prompts for your Windows password to store
the "run whether logged on or not" credential):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_scheduled_tasks.ps1
```

This registers four tasks (re-running is safe; re-run it whenever your
Windows password changes):

| Task | Schedule | Purpose |
|---|---|---|
| StockSage Bot | at startup | crash-restart supervisor loop around `main.py` (`scripts/run_bot_supervisor.cmd`) |
| StockSage Populate Outcomes | daily 02:00 | fills `alert_outcomes` after US close (`scripts/populate_outcomes.py`) |
| StockSage Watchlist Evaluation | daily 02:30 | nightly eligibility evaluation in apply mode |
| StockSage Auto Sync | every 15 min | `git pull --ff-only` from origin/main + bot restart when new commits land |

**Do not also launch `python main.py` manually while the tasks are
registered** — two instances fight over Telegram polling.

Logs: `logs/stocksage.log` (application, rotating), `logs/bot_supervisor.log`
(process starts/exits), `logs/auto_sync.log`, and
`logs/watchlist_evaluation_task.log`. Optional: set `HEALTHCHECK_PING_URL`
for a dead-man's-switch ping after every successful agent cycle.

---

## 📝 License

MIT License — see [LICENSE](LICENSE) for details.

---

## ⚠️ Disclaimer

StockSage is built for **educational and research purposes only**. It is not financial advice, and nothing in this project should be construed as a recommendation to buy or sell any security. Trading stocks involves significant risk. Past performance of any signal, score, or indicator does not guarantee future results. Always do your own research and consult a qualified financial advisor before making investment decisions.

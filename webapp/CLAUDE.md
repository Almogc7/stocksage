# webapp/CLAUDE.md

Rules for the StockSage web app (branch `feature/webapp`). The root
`CLAUDE.md` and `docs/DECISIONS.md` still apply in full.

## Hard rules
1. Do NOT modify existing bot files: `main.py`, `config.py`, `agent/`, `bot/`,
   `analyzers/`, `services/`, `scanners/`, `data/`, `db/`, `scripts/`,
   `tests/`. All new code lives under `webapp/`. If a change to an existing
   file seems necessary, stop and ask first.
2. The bot DB is READ-ONLY. Open it only through one helper using
   `sqlite3.connect("file:<path>?mode=ro", uri=True)`. No writes, no DDL,
   no migrations. The webapp's own state (layouts, UI settings) goes in a
   separate SQLite file under `webapp/data/` (gitignored).
3. Never run tests or dev servers against the real `db/stocksage.db`. Use a
   snapshot copy or a temp DB (same rule as the root CLAUDE.md).
4. Never print, log, or commit credentials, `.env` files, or any real DB.
5. Never commit runtime artifacts (`node_modules`, `dist`, `.venv`, `*.db`,
   logs). The server's auto-sync skips every cycle if its tree is dirty.
6. Do not edit the root `requirements.txt`. The webapp has its own
   dependency files.
7. This branch is never checked out on the server. It reaches `main` only
   through a reviewed PR.
8. Bind to `127.0.0.1` by default. No public exposure, and no non-local
   deployment before authentication exists.
9. Pattern detections are informational only: never alerts, and never
   written to the bot's tables, until each pattern has been backtested.
10. Charts use TradingView Lightweight Charts with our own data (DB
    `stock_prices` cache or yfinance). No TradingView scraping.

## Layout
- `webapp/backend/`  FastAPI app (+ `requirements.txt`, unittest tests)
- `webapp/frontend/` Vite + React + TypeScript
- `webapp/data/`     local state, gitignored

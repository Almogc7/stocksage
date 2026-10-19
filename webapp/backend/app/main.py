from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from . import bot_db, prices, settings, sparklines, state_db
from .patterns.detect import detect_patterns


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.bot_db_path()  # fail loudly at startup if misconfigured
    state_db.init_schema()
    yield


app = FastAPI(title="StockSage webapp", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    try:
        bot_db.ping()
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {"status": "ok", "bot_db": db_status}


@app.get("/api/watchlist")
def watchlist() -> list[dict]:
    return bot_db.fetch_watchlist()


@app.get("/api/prices/{symbol}")
async def get_prices(
    symbol: str,
    range: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = Query("1y"),
) -> dict:
    if not prices.SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    try:
        return await run_in_threadpool(prices.get_prices, symbol.upper(), range)
    except prices.NoData:
        raise HTTPException(status_code=404, detail="No price data for symbol")
    except prices.UpstreamError:
        raise HTTPException(status_code=502, detail="Price provider unavailable")


@app.get("/api/sparklines")
async def get_sparklines(
    symbols: str = Query(..., min_length=1),
    range: Literal["1mo", "3mo", "6mo", "1y"] = Query("3mo"),
) -> dict:
    raw_symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    seen: list[str] = []
    for s in raw_symbols:
        if s not in seen:
            seen.append(s)
    if not seen:
        raise HTTPException(status_code=422, detail="No symbols provided")
    if len(seen) > sparklines.SPARKLINE_MAX_SYMBOLS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many symbols (max {sparklines.SPARKLINE_MAX_SYMBOLS})",
        )
    for s in seen:
        if not prices.SYMBOL_RE.match(s):
            raise HTTPException(status_code=422, detail=f"Invalid symbol: {s}")
    data = await run_in_threadpool(sparklines.get_sparklines, seen, range)
    return {"range": range, "data": data}


@app.get("/api/patterns/{symbol}")
async def get_patterns(
    symbol: str,
    range: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = Query("2y"),
) -> dict:
    """Informational only — never written to any bot table, never an
    alert (webapp/CLAUDE.md rule 9)."""
    if not prices.SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    try:
        payload = await run_in_threadpool(prices.get_prices, symbol.upper(), range)
    except prices.NoData:
        raise HTTPException(status_code=404, detail="No price data for symbol")
    except prices.UpstreamError:
        raise HTTPException(status_code=502, detail="Price provider unavailable")
    detections = await run_in_threadpool(detect_patterns, payload, symbol.upper())
    return {"symbol": symbol.upper(), "range": range, "detections": detections}


@app.get("/api/signals/{symbol}")
async def get_signals(
    symbol: str,
    from_: str | None = Query(None, alias="from"),
    include_backtest: bool = Query(False),
) -> dict:
    if not prices.SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=422, detail="Invalid symbol")
    if from_ is not None and not bot_db.FROM_DATE_RE.match(from_):
        raise HTTPException(status_code=422, detail="Invalid from date")
    return await run_in_threadpool(
        bot_db.fetch_signals_for_symbol, symbol.upper(), from_, include_backtest
    )

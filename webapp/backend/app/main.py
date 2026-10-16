from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from . import bot_db, prices, settings, state_db


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

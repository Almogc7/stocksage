from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import bot_db, prices, settings, sparklines, state_db
from .patterns.detect import detect_patterns

_core_router = APIRouter()
_patterns_router = APIRouter()


@_core_router.get("/api/health")
def health() -> dict:
    try:
        bot_db.ping()
        db_status = "ok"
    except Exception:
        db_status = "error"
    return {"status": "ok", "bot_db": db_status}


@_core_router.get("/api/watchlist")
def watchlist() -> list[dict]:
    return bot_db.fetch_watchlist()


@_core_router.get("/api/prices/{symbol}")
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


@_core_router.get("/api/sparklines")
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


@_patterns_router.get("/api/patterns/{symbol}")
async def get_patterns(
    symbol: str,
    range: Literal["1mo", "3mo", "6mo", "1y", "2y", "5y"] = Query("2y"),
) -> dict:
    """Informational only — never written to any bot table, never an
    alert (webapp/CLAUDE.md rule 9). Dev-only: nothing in the frontend
    calls this, so it is not registered in production (see
    create_app())."""
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


@_core_router.get("/api/signals/{symbol}")
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


def _register_api_routes(app: FastAPI, production: bool) -> None:
    app.include_router(_core_router)
    if not production:
        app.include_router(_patterns_router)


def _check_production_prereqs() -> None:
    """Fail loudly at startup, before the app is usable, rather than
    surfacing a vague Starlette error on the first request."""
    dist_dir = settings.frontend_dist_path()
    index_html = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"
    if not index_html.is_file():
        raise RuntimeError(
            f"built frontend not found at {index_html}; run `npm run build` "
            "and copy dist/ there -- see webapp/deploy/RUNBOOK.md"
        )
    if not assets_dir.is_dir():
        raise RuntimeError(
            f"built frontend's assets/ folder not found at {assets_dir}; "
            "run `npm run build` and copy dist/ there -- see webapp/deploy/RUNBOOK.md"
        )
    db_path = settings.bot_db_path()  # also fails loudly if unset/missing
    if db_path.is_relative_to(settings.repo_db_dir()):
        raise RuntimeError(
            f"STOCKSAGE_DB_PATH ({db_path}) points inside the bot's own db/ "
            "directory -- point it at a snapshot copy instead, see "
            "webapp/deploy/RUNBOOK.md"
        )


def _lifespan_factory(production: bool):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not production:
            settings.bot_db_path()  # fail loudly at startup if misconfigured
        state_db.init_schema()
        yield

    return lifespan


class _ImmutableStaticFiles(StaticFiles):
    """Vite content-hashes every file under dist/assets/, so a long,
    immutable cache is safe for all of them."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _build_file_allowlist(dist_dir: Path) -> frozenset[str]:
    """POSIX-style relative paths of every real file under dist_dir,
    excluding assets/ (served separately by the StaticFiles mount).
    Computed once, from the filesystem, never from request input."""
    allowlist: set[str] = set()
    for path in dist_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(dist_dir)
        if rel.parts and rel.parts[0] == "assets":
            continue
        allowlist.add(rel.as_posix())
    return frozenset(allowlist)


def _register_spa_fallback(app: FastAPI) -> None:
    dist_dir = settings.frontend_dist_path().resolve()
    app.mount("/assets", _ImmutableStaticFiles(directory=dist_dir / "assets"), name="assets")
    allowlist = _build_file_allowlist(dist_dir)

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")

        # Membership in a precomputed allowlist -- never resolve(),
        # is_file(), or is_relative_to() on a request-derived path.
        if full_path in allowlist:
            return FileResponse(dist_dir / full_path)
        # The SPA shell itself must always revalidate so a new deploy is
        # picked up on next load (unlike the hashed assets above).
        return FileResponse(dist_dir / "index.html", headers={"Cache-Control": "no-cache"})


def create_app(production: bool | None = None) -> FastAPI:
    prod = settings.is_production() if production is None else production
    if prod:
        # Checked here, eagerly, before the FastAPI app (and the
        # StaticFiles mount, which would otherwise raise its own less
        # clear error) is even constructed -- not deferred to dev-mode
        # import time, and not left to surface from inside the lifespan.
        _check_production_prereqs()
    docs_kwargs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if prod else {}
    app = FastAPI(title="StockSage webapp", lifespan=_lifespan_factory(prod), **docs_kwargs)
    _register_api_routes(app, production=prod)
    if prod:
        _register_spa_fallback(app)
    return app


app = create_app()

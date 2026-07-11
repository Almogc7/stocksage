"""
Watchlist eligibility engine — relevance scoring and state-transition logic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from config import (
    ACTIVE_BANK_MAX,
    ACTIVE_MAX_SIZE,
    DEMOTION_CONSEC_REQUIRED,
    DEMOTION_THRESHOLD,
    DWELL_MIN_DAYS,
    ELIGIBILITY_MIN_AVG_VOLUME,
    ELIGIBILITY_MIN_DOLLAR_VOL,
    ELIGIBILITY_MIN_PRICE,
    ELIGIBILITY_STALE_DAYS,
    PROMOTION_CONSEC_REQUIRED,
    PROMOTION_THRESHOLD,
    REPLACEMENT_MARGIN,
)

_KNOWN_INDICES: frozenset[str] = frozenset({'^GSPC', '^IXIC', '^DJI', '^RUT', '^VIX'})
_KNOWN_ETFS: frozenset[str] = frozenset({
    'SPY', 'VOO', 'QQQ', 'VGT', 'XLK', 'SOXX', 'CIBR', 'ARKK', 'SCHG', 'UFO', 'NUKZ',
    'URA', 'URNM', 'NLR', 'REMX', 'COPX', 'CPER', 'SLX',
})


def classify_security_type(symbol: str) -> str:
    """Returns 'index', 'etf', 'crypto', or 'stock'."""
    s = symbol.upper()
    if s.startswith('^') or s in _KNOWN_INDICES:
        return 'index'
    if s in _KNOWN_ETFS:
        return 'etf'
    if s.endswith('-USD'):
        return 'crypto'
    return 'stock'


def compute_data_quality(price_data: dict | None, df) -> float:
    """Data quality score 0.0–1.0. Missing data contributes 0, never improves the score."""
    if price_data is None:
        return 0.0

    price = price_data.get('price', 0) or 0
    volume = price_data.get('volume', 0) or 0

    has_price = 0.35 if price > 0 else 0.0
    has_volume = 0.35 if volume > 0 else 0.0

    data_fresh = 0.0
    if df is not None and len(df) > 0:
        try:
            last_bar = df.index[-1]
            if hasattr(last_bar, 'tzinfo') and last_bar.tzinfo is not None:
                now = datetime.now(timezone.utc)
                delta = now - last_bar.to_pydatetime()
            else:
                now = datetime.utcnow()
                delta = now - last_bar.to_pydatetime()
            if delta.days <= ELIGIBILITY_STALE_DAYS:
                data_fresh = 0.30
        except Exception:
            pass

    return has_price + has_volume + data_fresh


def compute_liquidity_score(avg_volume: int, avg_price: float) -> float:
    """Liquidity score 0.0–1.0."""
    if avg_volume <= 0:
        return 0.0
    avg_dollar_vol = avg_price * avg_volume
    volume_score = min(avg_volume / ELIGIBILITY_MIN_AVG_VOLUME, 1.0)
    dvol_score = min(avg_dollar_vol / ELIGIBILITY_MIN_DOLLAR_VOL, 1.0)
    return (volume_score + dvol_score) / 2


def compute_trend_score(analysis: dict | None) -> float:
    """Trend relevance 0.0–1.0. Price above SMA150 and SMA150 above SMA200."""
    if analysis is None:
        return 0.0
    score = 0.0
    if analysis.get('above_sma150'):
        score += 0.5
    sma150 = analysis.get('sma150', 0) or 0
    sma200 = analysis.get('sma200', 0) or 0
    if sma150 > 0 and sma200 > 0 and sma150 > sma200:
        score += 0.5
    return score


def compute_momentum_score(analysis: dict | None) -> float:
    """Momentum score 0.0–1.0. Combines RSI zone and MACD crossover."""
    if analysis is None:
        return 0.0
    rsi = analysis.get('rsi', 0) or 0
    if 45 <= rsi <= 65:
        rsi_component = 1.0
    elif (35 <= rsi < 45) or (65 < rsi <= 75):
        rsi_component = 0.5
    else:
        rsi_component = 0.0

    crossover = analysis.get('crossover', '')
    if crossover == 'bullish':
        macd_component = 1.0
    elif crossover == 'bearish':
        macd_component = 0.0
    else:
        macd_component = 0.5

    return rsi_component * 0.6 + macd_component * 0.4


def compute_setup_proximity_score(analysis: dict | None) -> float:
    """Setup proximity 0.0–1.0 based on Bollinger Band position."""
    if analysis is None:
        return 0.0
    position = analysis.get('position', '')
    return {
        'middle':      1.0,
        'near_lower':  0.8,
        'near_upper':  0.4,
        'above_upper': 0.2,
        'below_lower': 0.2,
    }.get(position, 0.5)


def compute_volatility_score(analysis: dict | None) -> float:
    """Volatility suitability 0.0–1.0. ATR% 1.5–8 = 1.0, linear decrease outside."""
    if analysis is None:
        return 0.0
    try:
        atr_pct = float(str(analysis.get('atr_pct', 0) or 0).replace('%', ''))
    except (ValueError, TypeError):
        return 0.0

    if 1.5 <= atr_pct <= 8.0:
        return 1.0
    elif atr_pct < 1.5:
        return max(0.0, atr_pct / 1.5)
    else:
        return max(0.0, 1.0 - (atr_pct - 8.0) / (15.0 - 8.0))


def compute_relevance_score(
    symbol: str,
    price_data: dict | None,
    df,
    analysis: dict | None,
    avg_volume: int = 0,
) -> int:
    """
    Master relevance score 0–100.

    Weights: data_quality=25, liquidity=25, trend=20, momentum=15,
             setup_proximity=10, volatility=5

    Missing data contributes 0 to that component — it cannot improve the score.
    Returns 0 immediately if price_data is None or price == 0.
    """
    if price_data is None or (price_data.get('price', 0) or 0) == 0:
        return 0

    avg_price = price_data.get('price', 0) or 0
    data_q    = compute_data_quality(price_data, df)
    liquidity = compute_liquidity_score(avg_volume, avg_price)
    trend     = compute_trend_score(analysis)
    momentum  = compute_momentum_score(analysis)
    proximity = compute_setup_proximity_score(analysis)
    volatility = compute_volatility_score(analysis)

    raw = (
        data_q    * 25
        + liquidity * 25
        + trend     * 20
        + momentum  * 15
        + proximity * 10
        + volatility * 5
    )
    return max(0, min(100, int(round(raw))))


def determine_state_change(
    current_state: str,
    new_score: int,
    consec_promote: int,
    consec_demote: int,
    dwell_days: int,
    security_type: str,
    price_data: dict | None,
    avg_volume: int,
    is_bank: bool,
    active_count: int,
    active_bank_count: int,
    lowest_active_score: int | None,
) -> tuple[str, str]:
    """
    Determine the new wl_state and reason for a symbol after evaluation.
    Does NOT update the DB — callers are responsible for persistence.

    Returns (new_state, reason).
    """
    # USER_REMOVED is immutable — only explicit /add can change it
    if current_state == 'USER_REMOVED':
        return current_state, 'user removed'

    # ETF/index/crypto always stays ETF_INDEX_CONTEXT
    if security_type in ('etf', 'index', 'crypto'):
        return 'ETF_INDEX_CONTEXT', 'etf/index/crypto symbol'

    # Hard disqualifications → immediate TEMPORARILY_INELIGIBLE
    price = (price_data.get('price', 0) or 0) if price_data else 0
    if price_data is None or price == 0:
        return 'TEMPORARILY_INELIGIBLE', 'no price data'
    if price < ELIGIBILITY_MIN_PRICE:
        return 'TEMPORARILY_INELIGIBLE', f'price ${price:.2f} below minimum ${ELIGIBILITY_MIN_PRICE}'

    # Promotion check
    if current_state == 'MONITOR' and new_score >= PROMOTION_THRESHOLD:
        promote_count = consec_promote + 1
        if promote_count >= PROMOTION_CONSEC_REQUIRED:
            if is_bank and active_bank_count >= ACTIVE_BANK_MAX:
                return 'MONITOR', f'bank cap reached ({ACTIVE_BANK_MAX})'
            if active_count < ACTIVE_MAX_SIZE:
                return 'ACTIVE', (
                    f'score {new_score} >= {PROMOTION_THRESHOLD}'
                    f' for {promote_count} evaluations'
                )
            # Active is full — need replacement margin
            if lowest_active_score is not None and new_score >= lowest_active_score + REPLACEMENT_MARGIN:
                return 'ACTIVE', f'score {new_score} replaces lowest active {lowest_active_score}'
            return 'MONITOR', f'active full ({active_count}/{ACTIVE_MAX_SIZE}), no replacement margin'

    # Demotion check
    if current_state == 'ACTIVE' and new_score < DEMOTION_THRESHOLD:
        if dwell_days < DWELL_MIN_DAYS:
            return 'ACTIVE', f'dwell_days {dwell_days} < minimum {DWELL_MIN_DAYS}'
        demote_count = consec_demote + 1
        if demote_count >= DEMOTION_CONSEC_REQUIRED:
            return 'MONITOR', (
                f'score {new_score} < {DEMOTION_THRESHOLD}'
                f' for {demote_count} evaluations'
            )

    return current_state, 'no change'


def evaluate_symbol_eligibility(
    symbol: str,
    price_data: dict | None,
    df,
    avg_volume: int,
    current_state: str,
    consec_promote: int,
    consec_demote: int,
    dwell_days: int,
    security_type: str,
    is_bank: bool,
    active_count: int,
    active_bank_count: int,
    lowest_active_score: int | None,
) -> dict:
    """
    Top-level evaluation for a single symbol.

    Returns a dict with: symbol, score, new_state, reason, components.
    Does NOT update the DB.
    """
    from analyzers.technical import full_analysis

    analysis = None
    if df is not None and price_data is not None:
        price = price_data.get('price', 0) or 0
        if price > 0:
            try:
                analysis = full_analysis(symbol, df, price)
            except Exception:
                analysis = None

    score = compute_relevance_score(symbol, price_data, df, analysis, avg_volume)

    new_state, reason = determine_state_change(
        current_state=current_state,
        new_score=score,
        consec_promote=consec_promote,
        consec_demote=consec_demote,
        dwell_days=dwell_days,
        security_type=security_type,
        price_data=price_data,
        avg_volume=avg_volume,
        is_bank=is_bank,
        active_count=active_count,
        active_bank_count=active_bank_count,
        lowest_active_score=lowest_active_score,
    )

    avg_price = (price_data.get('price', 0) or 0) if price_data else 0
    components = {
        'data_quality': compute_data_quality(price_data, df),
        'liquidity':    compute_liquidity_score(avg_volume, avg_price),
        'trend':        compute_trend_score(analysis),
        'momentum':     compute_momentum_score(analysis),
        'proximity':    compute_setup_proximity_score(analysis),
        'volatility':   compute_volatility_score(analysis),
    }

    return {
        'symbol':    symbol,
        'score':     score,
        'new_state': new_state,
        'reason':    reason,
        'components': components,
    }

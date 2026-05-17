"""REVIEW stage: intelligent market selection.

Filters the full candidate set down to the most tradeable markets based on:
- Liquidity (volume_24h, bid/ask sizes)
- Spread (bid-ask gap as proxy for pricing efficiency)
- Time to resolution (avoid near-expiry and very long-dated)
- Topic family concentration (avoid overweight in one category)
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from ai_prophet_core.client_models import MarketData

from agent.config import ReviewConfig

logger = logging.getLogger(__name__)


def _spread(market: MarketData) -> float:
    return market.quote.best_ask - market.quote.best_bid


def _mid(market: MarketData) -> float:
    return (market.quote.best_bid + market.quote.best_ask) / 2.0


def _hours_to_resolution(market: MarketData) -> float:
    now = datetime.now(timezone.utc)
    delta = market.resolution_time - now
    return delta.total_seconds() / 3600.0


def select_markets(
    candidates: list[MarketData],
    open_market_ids: set[str],
    cash: float,
    equity: float,
    cfg: ReviewConfig,
) -> list[MarketData]:
    """Return filtered, ranked list of MarketData to evaluate."""
    # No point forecasting if we can't afford the minimum trade
    if cash < 0.50:
        logger.info("REVIEW: skipping — insufficient cash ($%.2f)", cash)
        return []

    # Scale max markets per tick down when equity is depleted (preserve compute budget)
    equity_ratio = min(cash / max(equity, 1.0), 1.0)
    effective_max = max(3, int(cfg.max_markets_per_tick * equity_ratio))

    scored: list[tuple[MarketData, float]] = []

    for market in candidates:
        # --- Hard filters ---
        hours_left = _hours_to_resolution(market)
        if hours_left < cfg.min_time_to_resolution_hours:
            continue
        if hours_left > cfg.max_time_to_resolution_days * 24:
            continue
        if market.quote.volume_24h < cfg.min_volume_24h:
            continue
        spread = _spread(market)
        if spread > cfg.max_spread:
            continue
        # Skip markets we already hold
        if market.market_id in open_market_ids:
            continue

        # --- Soft scoring ---
        liquidity_score = min(market.quote.volume_24h / 5000.0, 1.0)
        spread_score = 1.0 - (spread / cfg.max_spread)
        # Sweet spot: ~7 days to resolution
        time_score = max(0.0, 1.0 - abs(hours_left - 168.0) / 300.0)
        score = liquidity_score * 0.4 + spread_score * 0.4 + time_score * 0.2
        scored.append((market, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # Apply per-family concentration limit
    seen_families: Counter[str] = Counter()
    selected: list[MarketData] = []
    for market, _ in scored:
        family = market.family or market.topic or "other"
        if seen_families[family] >= cfg.max_positions_per_family:
            continue
        seen_families[family] += 1
        selected.append(market)
        if len(selected) >= effective_max:
            break

    logger.info(
        "REVIEW: %d candidates → %d selected",
        len(candidates),
        len(selected),
    )
    return selected

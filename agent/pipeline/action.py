"""ACTION stage: convert probability estimates into sized trade intents.

Uses fractional Kelly Criterion for optimal bet sizing:
  edge = |p_agent - market_mid|
  kelly_notional = (edge / counter_price) * kelly_fraction * available_cash
  shares = notional / entry_price

Only trades when edge > min_edge threshold, preventing noise trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ai_prophet_core import TradeAction, TradeSide
from ai_prophet_core.client_models import MarketData

from agent.config import ActionConfig, RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    market_id: str
    action: TradeAction
    side: TradeSide
    size: float          # notional dollars (converted to shares in loop.py)
    probability: float
    market_mid: float
    edge: float
    reasoning: str


def _kelly_notional(
    probability: float,
    mid: float,
    available_cash: float,
    kelly_fraction: float,
    max_notional: float,
) -> float:
    """Fractional Kelly notional bet size for a binary market."""
    if mid <= 0.0 or mid >= 1.0:
        return 0.0
    if probability > mid:
        # Bet YES: profit rate = (1 - mid)/mid per unit
        raw_kelly = (probability - mid) / (1.0 - mid)
    else:
        # Bet NO: profit rate = mid/(1 - mid) per unit
        raw_kelly = ((1.0 - probability) - (1.0 - mid)) / mid
    notional = max(0.0, raw_kelly) * kelly_fraction * available_cash
    return min(notional, max_notional)


def decide_trade(
    market: MarketData,
    probability: float,
    reasoning: str,
    cash: float,
    equity: float,
    open_positions_count: int,
    action_cfg: ActionConfig,
    risk_cfg: RiskConfig,
) -> TradeDecision | None:
    """Return a TradeDecision or None if no trade is warranted."""
    quote = market.quote
    mid = (quote.best_bid + quote.best_ask) / 2.0
    edge = abs(probability - mid)

    if edge < action_cfg.min_edge:
        logger.debug("SKIP %s: edge=%.3f < min=%.3f", market.market_id, edge, action_cfg.min_edge)
        return None

    if open_positions_count >= action_cfg.max_positions_total:
        logger.debug("SKIP %s: position limit reached", market.market_id)
        return None

    reserve = equity * risk_cfg.min_cash_reserve_pct
    available = max(0.0, cash - reserve)
    if available < 0.50:
        logger.debug("SKIP %s: insufficient cash", market.market_id)
        return None

    notional = _kelly_notional(
        probability=probability,
        mid=mid,
        available_cash=available,
        kelly_fraction=action_cfg.kelly_fraction,
        max_notional=action_cfg.max_position_notional,
    )
    if notional < 0.50:
        logger.debug("SKIP %s: notional too small ($%.2f)", market.market_id, notional)
        return None

    side = TradeSide.YES if probability > mid else TradeSide.NO
    logger.info(
        "TRADE %s: BUY %s | p=%.3f mid=%.3f edge=%.3f notional=$%.2f",
        market.market_id, side.value, probability, mid, edge, notional,
    )
    return TradeDecision(
        market_id=market.market_id,
        action=TradeAction.BUY,
        side=side,
        size=round(notional, 2),
        probability=probability,
        market_mid=mid,
        edge=edge,
        reasoning=reasoning,
    )


def decide_trades(
    markets_with_forecasts: list[tuple[MarketData, float, str]],
    cash: float,
    equity: float,
    open_positions_count: int,
    total_pnl: float,
    action_cfg: ActionConfig,
    risk_cfg: RiskConfig,
) -> list[TradeDecision]:
    """Batch trade decisions for all forecasted markets."""
    # Global drawdown halt
    if total_pnl < 0 and equity > 0:
        drawdown = abs(total_pnl) / (equity - total_pnl + 1e-9)
        if drawdown > risk_cfg.max_drawdown_pct:
            logger.warning("HALT: drawdown %.1f%% exceeds limit", drawdown * 100)
            return []

    # Sort by edge descending — highest conviction first
    sorted_markets = sorted(
        markets_with_forecasts,
        key=lambda x: abs(x[1] - (x[0].quote.best_bid + x[0].quote.best_ask) / 2.0),
        reverse=True,
    )

    decisions: list[TradeDecision] = []
    running_positions = open_positions_count
    running_cash = cash

    for market, probability, reasoning in sorted_markets:
        decision = decide_trade(
            market=market,
            probability=probability,
            reasoning=reasoning,
            cash=running_cash,
            equity=equity,
            open_positions_count=running_positions,
            action_cfg=action_cfg,
            risk_cfg=risk_cfg,
        )
        if decision:
            decisions.append(decision)
            running_cash -= decision.size
            running_positions += 1

    return decisions

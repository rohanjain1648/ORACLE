"""Core agent loop: experiment → tick → pipeline → submit."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ai_prophet_core import (
    ServerAPIClient,
    TradeIntentRequest,
    TradeSide,
)
from ai_prophet_core.client_models import MarketData

from agent.config import OracleConfig
from agent.pipeline.action import TradeDecision, decide_trades
from agent.pipeline.forecast import estimate_probability
from agent.pipeline.review import select_markets
from agent.pipeline.search import gather_evidence
from agent.utils.search import make_search_provider

logger = logging.getLogger(__name__)

_LEASE_OWNER = f"oracle-{uuid.uuid4().hex[:8]}"


def _f(val: str | float) -> float:
    """Coerce SDK decimal-string fields to float."""
    return float(val)


@dataclass
class TickStats:
    tick_num: int
    candidates: int
    selected: int
    forecasted: int
    trades_submitted: int
    equity_before: float
    equity_after: float = 0.0
    errors: list[str] = field(default_factory=list)


def _shares_str(decision: TradeDecision, market: MarketData) -> str:
    """Convert notional dollars to a share count string.

    YES shares cost best_ask each.
    NO shares cost (1 - best_bid) each.
    """
    quote = market.quote
    if decision.side == TradeSide.YES:
        price = max(_f(quote.best_ask), 0.01)
    else:
        price = max(1.0 - _f(quote.best_bid), 0.01)
    shares = decision.size / price
    return f"{max(shares, 0.01):.4f}"


def _forecast_market(
    market: MarketData,
    cfg: OracleConfig,
    search_provider,
) -> tuple[MarketData, float, str] | None:
    """Run SEARCH + FORECAST for one market. Returns None on failure."""
    try:
        evidence = gather_evidence(
            market=market,
            search_provider=search_provider,
            search_cfg=cfg.search,
            forecast_cfg=cfg.forecast,
            anthropic_api_key=cfg.anthropic_api_key,
        )
        probability, reasoning, confidence = estimate_probability(
            market=market,
            cfg=cfg.forecast,
            evidence=evidence,
            anthropic_api_key=cfg.anthropic_api_key,
            openai_api_key=cfg.openai_api_key,
        )
        bid = _f(market.quote.best_bid)
        ask = _f(market.quote.best_ask)
        logger.info(
            "FORECAST %s | p=%.3f conf=%s mid=%.3f",
            market.market_id, probability, confidence, (bid + ask) / 2.0,
        )
        return market, probability, reasoning
    except Exception as e:
        logger.error("Forecast failed for %s: %s", market.market_id, e)
        return None


def run_tick(
    client: ServerAPIClient,
    experiment_id: str,
    participant_idx: int,
    tick_num: int,
    cfg: OracleConfig,
    search_provider,
) -> TickStats:
    logger.info("=== TICK %d START ===", tick_num)

    # 1. Claim tick
    claim = client.claim_tick(experiment_id=experiment_id, lease_owner_id=_LEASE_OWNER)
    if claim.no_tick_available or claim.tick_id is None:
        wait = claim.retry_after_sec or 60
        logger.info("No tick available (%s). Waiting %ds.", claim.reason, wait)
        time.sleep(wait)
        return TickStats(tick_num=tick_num, candidates=0, selected=0,
                         forecasted=0, trades_submitted=0, equity_before=0.0)

    tick_id: str = claim.tick_id

    # 2. Portfolio + candidates
    portfolio = client.get_portfolio(experiment_id=experiment_id, participant_idx=participant_idx)
    cash = _f(portfolio.cash) if portfolio else cfg.starting_cash
    equity = _f(portfolio.equity) if portfolio else cfg.starting_cash
    total_pnl = _f(portfolio.total_pnl) if portfolio else 0.0
    open_positions = portfolio.positions if portfolio else []
    open_market_ids = {p.market_id for p in open_positions}

    tick_ts = datetime.now(timezone.utc)
    candidates_resp = client.get_candidates(tick_ts=tick_ts)
    raw_markets: list[MarketData] = candidates_resp.markets
    candidate_set_id: str = candidates_resp.candidate_set_id

    stats = TickStats(
        tick_num=tick_num,
        candidates=len(raw_markets),
        selected=0, forecasted=0, trades_submitted=0,
        equity_before=equity,
    )
    logger.info("Portfolio: cash=$%.2f equity=$%.2f positions=%d",
                cash, equity, len(open_positions))

    # 3. REVIEW
    selected = select_markets(
        candidates=raw_markets,
        open_market_ids=open_market_ids,
        cash=cash,
        equity=equity,
        cfg=cfg.review,
    )
    stats.selected = len(selected)

    if not selected:
        logger.info("No tradeable markets this tick.")
        client.complete_tick(experiment_id=experiment_id, tick_id=tick_id)
        return stats

    # 4. SEARCH + FORECAST in parallel
    forecasts: list[tuple[MarketData, float, str]] = []
    with ThreadPoolExecutor(max_workers=min(len(selected), 5)) as executor:
        futures = {
            executor.submit(_forecast_market, market, cfg, search_provider): market.market_id
            for market in selected
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                forecasts.append(result)

    stats.forecasted = len(forecasts)

    # 5. ACTION
    decisions: list[TradeDecision] = decide_trades(
        markets_with_forecasts=forecasts,
        cash=cash,
        equity=equity,
        open_positions_count=len(open_positions),
        total_pnl=total_pnl,
        action_cfg=cfg.action,
        risk_cfg=cfg.risk,
    )

    # 6. Submit intents
    if decisions:
        market_by_id = {m.market_id: m for m in selected}
        intents = [
            TradeIntentRequest(
                market_id=d.market_id,
                action=d.action.value,
                side=d.side.value,
                shares=_shares_str(d, market_by_id[d.market_id]),
                idempotency_key=f"{tick_id}-{d.market_id}-{d.side.value}",
            )
            for d in decisions
            if d.market_id in market_by_id
        ]
        try:
            client.submit_trade_intents(
                experiment_id=experiment_id,
                participant_idx=participant_idx,
                tick_id=tick_id,
                candidate_set_id=candidate_set_id,
                intents=intents,
            )
            stats.trades_submitted = len(intents)
            logger.info("Submitted %d intents", len(intents))
        except Exception as e:
            logger.error("Intent submission failed: %s", e)
            stats.errors.append(str(e))

    # 7. Finalize tick
    client.complete_tick(experiment_id=experiment_id, tick_id=tick_id)

    try:
        updated = client.get_portfolio(experiment_id=experiment_id, participant_idx=participant_idx)
        if updated:
            stats.equity_after = _f(updated.equity)
            logger.info("=== TICK %d END | equity=$%.2f pnl=$%.2f trades=%d ===",
                        tick_num, stats.equity_after, _f(updated.total_pnl),
                        stats.trades_submitted)
    except Exception:
        stats.equity_after = equity

    return stats


def run_experiment(cfg: OracleConfig) -> None:
    client = ServerAPIClient(base_url=cfg.pa_server_url, api_key=cfg.pa_server_api_key)
    search_provider = make_search_provider(cfg.search.provider, cfg.tavily_api_key)

    client.health_check()
    logger.info("Prophet Arena API: OK")

    config_dict = {"model": cfg.forecast.primary_model, "kelly": cfg.action.kelly_fraction}
    config_json_str = json.dumps(config_dict, sort_keys=True)
    config_hash = hashlib.sha256(config_json_str.encode()).hexdigest()[:16]

    experiment = client.create_or_get_experiment(
        slug=cfg.experiment_slug,
        config_hash=config_hash,
        config_json=config_dict,
        n_ticks=cfg.max_ticks,
    )
    experiment_id = experiment.experiment_id
    logger.info("Experiment: %s (id=%s)", cfg.experiment_slug, experiment_id)

    participant = client.upsert_participant(
        experiment_id=experiment_id,
        model=cfg.forecast.primary_model,
        rep=0,
        starting_cash=cfg.starting_cash,
    )
    participant_idx = participant.participant_idx
    logger.info("Participant idx=%d model=%s", participant_idx, cfg.forecast.primary_model)

    all_stats: list[TickStats] = []
    for tick_num in range(1, cfg.max_ticks + 1):
        try:
            stats = run_tick(
                client=client,
                experiment_id=experiment_id,
                participant_idx=participant_idx,
                tick_num=tick_num,
                cfg=cfg,
                search_provider=search_provider,
            )
            all_stats.append(stats)
        except Exception as e:
            logger.error("Tick %d failed: %s", tick_num, e)
            time.sleep(10)

    total_trades = sum(s.trades_submitted for s in all_stats)
    final_equity = all_stats[-1].equity_after if all_stats else cfg.starting_cash
    logger.info("COMPLETE | ticks=%d trades=%d equity=$%.2f pnl=$%.2f",
                len(all_stats), total_trades, final_equity, final_equity - cfg.starting_cash)

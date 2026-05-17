"""Core agent loop using BenchmarkSession (the recommended high-level client)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ai_prophet_core import ServerAPIClient, TradeIntentRequest
from ai_prophet_core.arena import BenchmarkSession, TickLease, TickCandidates
from ai_prophet_core.client_models import MarketData

from agent.config import OracleConfig
from agent.pipeline.action import TradeDecision, decide_trades
from agent.pipeline.forecast import estimate_probability
from agent.pipeline.review import select_markets
from agent.pipeline.search import gather_evidence
from agent.utils.search import make_search_provider

logger = logging.getLogger(__name__)

# Hard limits from the Prophet Arena trading harness
_MAX_INTENTS_PER_TICK = 20


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
    """Convert notional dollars to a share-count string.

    YES shares cost best_ask each.
    NO  shares cost (1 - best_bid) each.
    """
    from ai_prophet_core import TradeSide
    quote = market.quote
    if decision.side == TradeSide.YES:
        price = max(_f(quote.best_ask), 0.01)
    else:
        price = max(1.0 - _f(quote.best_bid), 0.01)
    shares = decision.size / price
    return f"{max(shares, 0.01):.4f}"


def _forecast_one(
    market: MarketData,
    cfg: OracleConfig,
    search_provider,
) -> tuple[MarketData, float, str] | None:
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
        logger.info("FORECAST %s | p=%.3f conf=%s mid=%.3f",
                    market.market_id, probability, confidence, (bid + ask) / 2)
        return market, probability, reasoning
    except Exception as e:
        logger.error("Forecast failed for %s: %s", market.market_id, e)
        return None


def run_tick(
    session: BenchmarkSession,
    lease: TickLease,
    participant_idx: int,
    tick_num: int,
    cfg: OracleConfig,
    search_provider,
) -> TickStats:
    logger.info("=== TICK %d START ===", tick_num)

    # 2. Portfolio + candidates
    portfolio = session.get_portfolio(participant_idx)
    cash   = _f(portfolio.cash)   if portfolio else cfg.starting_cash
    equity = _f(portfolio.equity) if portfolio else cfg.starting_cash
    total_pnl = _f(portfolio.total_pnl) if portfolio else 0.0
    open_positions = portfolio.positions if portfolio else []
    open_market_ids = {p.market_id for p in open_positions}

    tick_candidates: TickCandidates = session.load_candidates(lease)
    raw_markets: list[MarketData] = tick_candidates.candidates.markets

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
        session.finalize(lease, participant_idx)
        session.complete_tick(lease)
        return stats

    # 4. SEARCH + FORECAST in parallel
    forecasts: list[tuple[MarketData, float, str]] = []
    with ThreadPoolExecutor(max_workers=min(len(selected), 5)) as executor:
        futures = {
            executor.submit(_forecast_one, market, cfg, search_provider): market.market_id
            for market in selected
        }
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                forecasts.append(result)

    stats.forecasted = len(forecasts)

    # 5. ACTION — Kelly-sized decisions
    decisions: list[TradeDecision] = decide_trades(
        markets_with_forecasts=forecasts,
        cash=cash,
        equity=equity,
        open_positions_count=len(open_positions),
        total_pnl=total_pnl,
        action_cfg=cfg.action,
        risk_cfg=cfg.risk,
    )

    # 6. Submit intents (hard cap: 20 per tick from harness rules)
    if decisions:
        market_by_id = {m.market_id: m for m in selected}
        intents = [
            TradeIntentRequest(
                market_id=d.market_id,
                action=d.action.value,
                side=d.side.value,
                shares=_shares_str(d, market_by_id[d.market_id]),
                idempotency_key="",   # auto-filled by BenchmarkSession
            )
            for d in decisions
            if d.market_id in market_by_id
        ][:_MAX_INTENTS_PER_TICK]

        try:
            session.submit_intents(lease, participant_idx, intents)
            stats.trades_submitted = len(intents)
            logger.info("Submitted %d intents", len(intents))
        except Exception as e:
            logger.error("Intent submission failed: %s", e)
            stats.errors.append(str(e))

    # 7. Finalize participant then complete tick (required order)
    session.finalize(lease, participant_idx)
    session.complete_tick(lease)

    # 8. Refresh portfolio for logging
    try:
        updated = session.get_portfolio(participant_idx)
        if updated:
            stats.equity_after = _f(updated.equity)
            logger.info("=== TICK %d END | equity=$%.2f pnl=$%.2f trades=%d ===",
                        tick_num, stats.equity_after, _f(updated.total_pnl),
                        stats.trades_submitted)
    except Exception:
        stats.equity_after = equity

    return stats


def run_experiment(cfg: OracleConfig) -> None:
    api = ServerAPIClient(base_url=cfg.pa_server_url, api_key=cfg.pa_server_api_key)
    session = BenchmarkSession(api)
    search_provider = make_search_provider(cfg.search.provider, cfg.tavily_api_key)

    api.health_check()
    logger.info("Prophet Arena API: OK")

    # Deterministic config hash for idempotent experiment creation
    config_dict = {
        "model": cfg.forecast.primary_model,
        "kelly": cfg.action.kelly_fraction,
        "min_edge": cfg.action.min_edge,
    }
    config_hash = hashlib.sha256(
        json.dumps(config_dict, sort_keys=True).encode()
    ).hexdigest()[:16]

    session.create_experiment(
        slug=cfg.experiment_slug,
        config_hash=config_hash,
        config_json=config_dict,
        n_ticks=cfg.max_ticks,
    )
    logger.info("Experiment: %s  (n_ticks=%d)", cfg.experiment_slug, cfg.max_ticks)

    part = session.upsert_participant(
        model=cfg.forecast.primary_model,
        rep=0,
        starting_cash=cfg.starting_cash,
    )
    participant_idx: int = part.participant_idx
    logger.info("Participant idx=%d  model=%s  cash=$%.0f",
                participant_idx, cfg.forecast.primary_model, cfg.starting_cash)

    all_stats: list[TickStats] = []
    tick_num = 0

    while True:
        tick_num += 1
        try:
            # Peek at the lease first so we can break cleanly on experiment end
            lease: TickLease = session.claim_tick()

            if not lease.available:
                if lease.reason == "experiment_completed":
                    logger.info("Experiment completed — exiting loop.")
                    break
                wait = lease.retry_after_sec or 15
                logger.info("No tick yet (%s). Waiting %ds.", lease.reason, wait)
                time.sleep(wait)
                tick_num -= 1   # don't count the wait as a tick
                continue

            stats = run_tick(
                session=session,
                lease=lease,
                participant_idx=participant_idx,
                tick_num=tick_num,
                cfg=cfg,
                search_provider=search_provider,
            )
            all_stats.append(stats)

        except Exception as e:
            logger.error("Tick %d failed: %s", tick_num, e)
            time.sleep(15)

    total_trades = sum(s.trades_submitted for s in all_stats)
    final_equity = all_stats[-1].equity_after if all_stats else cfg.starting_cash
    logger.info("COMPLETE | ticks=%d trades=%d equity=$%.2f pnl=$%.2f",
                len(all_stats), total_trades, final_equity,
                final_equity - cfg.starting_cash)

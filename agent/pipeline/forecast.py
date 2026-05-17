"""FORECAST stage: superforecaster-style probability estimation.

Structured decomposition:
1. Reference class / base rate
2. Inside view (specific evidence)
3. Outside view (comparable past events)
4. Market anchor (how much to update from market price)
5. Calibration check (overconfidence correction)
"""

from __future__ import annotations

import logging
import re

from ai_prophet_core.client_models import MarketData

from agent.config import ForecastConfig
from agent.utils.llm import call_anthropic, call_openai, extract_json

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a world-class superforecaster trained in the methods of Philip Tetlock's Good Judgment Project.
Estimate the probability that a prediction market question resolves YES.

Follow this structured process:
1. REFERENCE CLASS: Category of event and base rate of YES resolution.
2. INSIDE VIEW: Specific evidence pointing toward YES vs NO.
3. OUTSIDE VIEW: How unusual would YES be compared to similar past events?
4. MARKET ANCHOR: The market prices this at {mid:.1%}. How much should you update from this prior?
5. CALIBRATION CHECK: Are you overconfident? Anchoring too much on the market price?
6. FINAL ESTIMATE: A probability in [0.02, 0.98].

Respond ONLY with a JSON object:
{{
  "reasoning": "2-3 sentence summary",
  "probability": <float>,
  "confidence": "low|medium|high"
}}
"""

_USER = """\
Question: {question}

Description: {description}

Resolves: {resolution_time}

Market:
  Best bid (YES): {best_bid:.3f}
  Best ask (YES): {best_ask:.3f}
  Mid:            {mid:.3f}
  24h volume:     ${volume_24h:.0f}

Evidence:
{evidence}

Provide your probability estimate as JSON.
"""


def _parse(text: str) -> tuple[float, str, str]:
    try:
        raw = extract_json(text)
        data: dict = raw if isinstance(raw, dict) else {}
        prob = max(0.02, min(0.98, float(data["probability"])))
        return prob, str(data.get("reasoning", "")), str(data.get("confidence", "medium"))
    except Exception as e:
        logger.warning("Forecast parse error: %s | text: %.200s", e, text)
        match = re.search(r"0\.\d+", text)
        return (float(match.group()) if match else 0.5), "parse error", "low"


def _call(
    market: MarketData,
    evidence: str,
    cfg: ForecastConfig,
    model_spec: str,
    anthropic_api_key: str,
    openai_api_key: str,
    use_thinking: bool = False,
) -> tuple[float, str, str]:
    bid = float(market.quote.best_bid)
    ask = float(market.quote.best_ask)
    mid = (bid + ask) / 2.0

    system = _SYSTEM.format(mid=mid)
    user = _USER.format(
        question=market.question,
        description=market.description or "(no description)",
        resolution_time=market.resolution_time.strftime("%Y-%m-%d %H:%M UTC"),
        best_bid=bid,
        best_ask=ask,
        mid=mid,
        volume_24h=market.quote.volume_24h,
        evidence=evidence,
    )

    provider, model_name = model_spec.split(":", 1) if ":" in model_spec else ("anthropic", model_spec)

    if provider == "anthropic":
        text = call_anthropic(
            prompt=user,
            system=system,
            api_key=anthropic_api_key,
            model=model_name,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            thinking_budget=cfg.thinking_budget_tokens if use_thinking else 0,
        )
    else:
        text = call_openai(
            prompt=user,
            system=system,
            api_key=openai_api_key,
            model=model_name,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
    return _parse(text)


def estimate_probability(
    market: MarketData,
    cfg: ForecastConfig,
    evidence: str,
    anthropic_api_key: str,
    openai_api_key: str = "",
) -> tuple[float, str, str]:
    """Return (probability, reasoning, confidence) for the market resolving YES."""
    primary_prob, reasoning, confidence = _call(
        market=market,
        evidence=evidence,
        cfg=cfg,
        model_spec=cfg.primary_model,
        anthropic_api_key=anthropic_api_key,
        openai_api_key=openai_api_key,
        use_thinking=True,
    )

    if not cfg.ensemble_models:
        return primary_prob, reasoning, confidence

    weighted_sum = primary_prob * cfg.ensemble_weight_primary
    total_weight = cfg.ensemble_weight_primary
    per_weight = (1.0 - cfg.ensemble_weight_primary) / len(cfg.ensemble_models)

    for model_spec in cfg.ensemble_models:
        try:
            result = _call(
                market=market,
                evidence=evidence,
                cfg=cfg,
                model_spec=model_spec,
                anthropic_api_key=anthropic_api_key,
                openai_api_key=openai_api_key,
            )
            prob = result[0]
            weighted_sum += prob * per_weight
            total_weight += per_weight
        except Exception as e:
            logger.warning("Ensemble model %s failed: %s", model_spec, e)

    ensemble_prob = max(0.02, min(0.98, weighted_sum / total_weight))
    logger.debug("FORECAST %s: primary=%.3f ensemble=%.3f",
                 market.market_id, primary_prob, ensemble_prob)
    return ensemble_prob, reasoning, confidence

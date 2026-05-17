"""SEARCH stage: web research to gather evidence for forecasting."""

from __future__ import annotations

import logging

from ai_prophet_core.client_models import MarketData

from agent.config import SearchConfig, ForecastConfig
from agent.utils.llm import call_anthropic
from agent.utils.search import SearchProvider, format_search_results

logger = logging.getLogger(__name__)


def _generate_queries(market: MarketData, n: int) -> list[str]:
    base = market.question.strip().rstrip("?")
    queries = [base]
    if n >= 2:
        queries.append(f"{base} latest news 2026")
    if n >= 3:
        queries.append(f"{base} prediction probability")
    return queries[:n]


def _llm_synthesize(
    market: MarketData,
    raw_context: str,
    api_key: str,
    model: str,
    temperature: float,
) -> str:
    if not raw_context.strip() or raw_context == "No search results available.":
        return "No external evidence found."
    system = (
        "You are a research analyst helping a prediction market trader. "
        "Summarize the evidence below in 2-4 sentences. "
        "Focus only on facts relevant to whether the event resolves YES or NO. "
        "Be specific and quantitative where possible. Do not editorialize."
    )
    prompt = (
        f"Market question: {market.question}\n\n"
        f"Search results:\n{raw_context}\n\n"
        "Write a concise evidence summary (2-4 sentences):"
    )
    _, model_name = model.split(":", 1) if ":" in model else ("anthropic", model)
    return call_anthropic(
        prompt=prompt,
        system=system,
        api_key=api_key,
        model=model_name,
        temperature=temperature,
        max_tokens=400,
    )


def gather_evidence(
    market: MarketData,
    search_provider: SearchProvider,
    search_cfg: SearchConfig,
    forecast_cfg: ForecastConfig,
    anthropic_api_key: str,
) -> str:
    """Return a synthesized evidence paragraph for a single market."""
    if not search_cfg.enabled:
        return "Web search disabled."

    queries = _generate_queries(market, search_cfg.queries_per_market)
    all_results = []
    for q in queries:
        try:
            results = search_provider.search(q, search_cfg.max_results_per_query)
            all_results.extend(results)
        except Exception as e:
            logger.warning("Search failed for '%s': %s", q, e)

    seen_urls: set[str] = set()
    unique_results = [
        r for r in all_results
        if r.url not in seen_urls and not seen_urls.add(r.url)  # type: ignore[func-returns-value]
    ]

    raw_context = format_search_results(unique_results)
    return _llm_synthesize(
        market=market,
        raw_context=raw_context,
        api_key=anthropic_api_key,
        model=forecast_cfg.primary_model,
        temperature=forecast_cfg.temperature,
    )

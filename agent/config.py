"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class ReviewConfig(BaseModel):
    max_markets_per_tick: int = 20
    min_volume_24h: float = 50.0
    max_spread: float = 0.15
    max_time_to_resolution_days: int = 30
    min_time_to_resolution_hours: int = 1
    max_positions_per_family: int = 3


class SearchConfig(BaseModel):
    enabled: bool = True
    provider: str = "tavily"
    queries_per_market: int = 2
    max_results_per_query: int = 5


class ForecastConfig(BaseModel):
    primary_model: str = "anthropic:claude-sonnet-4-6"
    ensemble_models: list[str] = Field(default_factory=list)
    ensemble_weight_primary: float = 0.75
    temperature: float = 0.3
    max_tokens: int = 1500
    thinking_budget_tokens: int = 5000


class ActionConfig(BaseModel):
    min_edge: float = 0.05
    kelly_fraction: float = 0.25
    max_position_notional: float = 100.0   # harness max exposure: $1,000/market
    max_positions_total: int = 28          # harness hard limit: 30


class RiskConfig(BaseModel):
    max_drawdown_pct: float = 0.30
    min_cash_reserve_pct: float = 0.20


class OracleConfig(BaseModel):
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    action: ActionConfig = Field(default_factory=ActionConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Runtime settings from env
    pa_server_api_key: str = Field(default_factory=lambda: os.environ["PA_SERVER_API_KEY"])
    pa_server_url: str = Field(default_factory=lambda: os.environ.get("PA_SERVER_URL", "https://api.aiprophet.dev"))
    anthropic_api_key: str = Field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = Field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    tavily_api_key: str = Field(default_factory=lambda: os.environ.get("TAVILY_API_KEY", ""))

    experiment_slug: str = Field(default_factory=lambda: os.environ.get("ORACLE_SLUG", "eval_oracle"))
    max_ticks: int = Field(default_factory=lambda: int(os.environ.get("ORACLE_MAX_TICKS", "1500")))
    starting_cash: float = Field(default_factory=lambda: float(os.environ.get("ORACLE_STARTING_CASH", "10000.0")))
    replicates: int = Field(default_factory=lambda: int(os.environ.get("ORACLE_REPLICATES", "1")))


def load_config(path: str | Path = "config.yaml") -> OracleConfig:
    config_path = Path(path)
    raw: dict = {}
    if config_path.exists():
        with open(config_path) as f:
            raw = yaml.safe_load(f) or {}
    return OracleConfig(**raw)

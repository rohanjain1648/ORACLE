"""ORACLE — Prophet Hacks 2026 Trading Track entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.text import Text

from agent.config import load_config
from agent.loop import run_experiment

console = Console()

BANNER = """\
 ██████  ██████   █████   ██████ ██      ███████
██    ██ ██   ██ ██   ██ ██      ██      ██
██    ██ ██████  ███████ ██      ██      █████
██    ██ ██   ██ ██   ██ ██      ██      ██
 ██████  ██   ██ ██   ██  ██████ ███████ ███████

Optimal Reasoning Agent for Calibrated Leveraged Execution
Prophet Hacks 2026 · Trading Track
"""


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    # Quiet noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ORACLE: AI trading agent for Prophet Arena prediction markets"
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Path to config YAML (default: config.yaml)"
    )
    parser.add_argument(
        "--slug", help="Experiment slug (overrides config + ORACLE_SLUG env var)"
    )
    parser.add_argument(
        "--ticks", type=int, help="Max ticks to run (overrides config)"
    )
    parser.add_argument(
        "--cash", type=float, help="Starting cash in dollars (overrides config)"
    )
    parser.add_argument(
        "--no-search", action="store_true", help="Disable web search (faster, lower cost)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )
    args = parser.parse_args()

    setup_logging(args.verbose)
    console.print(Panel(Text(BANNER, style="bold cyan"), border_style="cyan"))

    cfg = load_config(args.config)

    # CLI overrides
    if args.slug:
        cfg.experiment_slug = args.slug
    if args.ticks:
        cfg.max_ticks = args.ticks
    if args.cash:
        cfg.starting_cash = args.cash
    if args.no_search:
        cfg.search.enabled = False

    console.print(f"[bold]Experiment:[/bold] {cfg.experiment_slug}")
    console.print(f"[bold]Model:[/bold]      {cfg.forecast.primary_model}")
    console.print(f"[bold]Max ticks:[/bold]  {cfg.max_ticks}")
    console.print(f"[bold]Cash:[/bold]       ${cfg.starting_cash:.2f}")
    console.print(f"[bold]Search:[/bold]     {'on (' + cfg.search.provider + ')' if cfg.search.enabled else 'off'}")
    console.print(f"[bold]Kelly:[/bold]      {cfg.action.kelly_fraction:.0%} fractional")
    console.print()

    try:
        run_experiment(cfg)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"[bold red]Fatal error:[/bold red] {e}")
        logging.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()

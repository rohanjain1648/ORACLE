#!/usr/bin/env bash
# ORACLE — Prophet Hacks 2026 Trading Track
# Run script for evaluation harness judges.
#
# Usage:
#   ./run.sh                        # Full run (96 ticks, web search on)
#   ./run.sh --no-search            # No web search (faster, lower cost)
#   ./run.sh --ticks 10             # Short smoke-test run
#   ./run.sh --slug my-experiment   # Custom experiment name
#
# Required env vars (set in .env or export before running):
#   PA_SERVER_API_KEY   — Prophet Arena API key
#   ANTHROPIC_API_KEY   — Anthropic API key (Claude)
#   TAVILY_API_KEY      — Tavily search API key (optional)
#   OPENAI_API_KEY      — OpenAI API key (optional ensemble model)

set -euo pipefail

# ── Environment setup ────────────────────────────────────────────────────────
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

# ── Python version check ─────────────────────────────────────────────────────
PYTHON=$(command -v python3.11 2>/dev/null || command -v python3 2>/dev/null || command -v python 2>/dev/null)
if [ -z "$PYTHON" ]; then
  echo "Error: Python 3.11+ is required"
  exit 1
fi
PY_VERSION=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Using Python $PY_VERSION at $PYTHON"

# ── Virtual environment ───────────────────────────────────────────────────────
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment..."
  $PYTHON -m venv "$VENV_DIR"
fi

# Activate
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
  source "$VENV_DIR/Scripts/activate"
else
  source "$VENV_DIR/bin/activate"
fi

# ── Install dependencies ──────────────────────────────────────────────────────
echo "Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -e .

# ── Validate required API keys ───────────────────────────────────────────────
if [ -z "${PA_SERVER_API_KEY:-}" ]; then
  echo "Error: PA_SERVER_API_KEY is not set."
  echo "Get your key at https://prophetarena.co/developer"
  exit 1
fi

if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "Error: ANTHROPIC_API_KEY is not set."
  exit 1
fi

# ── Launch the agent ──────────────────────────────────────────────────────────
echo ""
echo "Starting ORACLE trading agent..."
echo ""
python -m agent.main "$@"

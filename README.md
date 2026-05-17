# ORACLE — Optimal Reasoning Agent for Calibrated Leveraged Execution

**Prophet Hacks 2026 · Trading Track**

ORACLE is an AI trading agent that autonomously takes positions on prediction markets using a structured four-stage pipeline: **market selection → web research → superforecaster probability estimation → Kelly-optimal trade sizing**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    ORACLE Agent Loop                        │
│  (runs every ~15 min tick for up to 96 ticks / 2 weeks)    │
└──────────────────────┬──────────────────────────────────────┘
                       │
         ┌─────────────▼─────────────┐
         │    1. REVIEW              │  Filter raw Kalshi markets by:
         │    Market Selection       │  liquidity · spread · time · family
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │    2. SEARCH              │  Tavily web search per market →
         │    Evidence Gathering     │  LLM synthesis → evidence paragraph
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │    3. FORECAST            │  Superforecaster-style decomposition:
         │    Probability Estimation │  base rate · inside/outside view ·
         │                           │  market anchor · calibration check
         │                           │  → probability p ∈ [0.02, 0.98]
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │    4. ACTION              │  Fractional Kelly Criterion:
         │    Trade Sizing           │  edge = |p − market_mid|
         │                           │  notional = kelly × 25% × cash
         │                           │  Risk guards: drawdown halt,
         │                           │  cash reserve, position limits
         └─────────────┬─────────────┘
                       │
         ┌─────────────▼─────────────┐
         │  submit_intents()         │  ai-prophet-core SDK → Prophet Arena
         └───────────────────────────┘
```

### Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Forecasting** | Claude claude-sonnet-4-6 with extended thinking | Best calibration on complex real-world events |
| **Search** | Tavily advanced search, 2 queries/market | Fresh context beats stale priors for fast-moving events |
| **Position sizing** | 25% fractional Kelly | Full Kelly is too aggressive; 25% provides 4× margin of safety |
| **Edge threshold** | ≥ 5% from market mid | Prevents noise trading and slippage losses |
| **Ensemble** | Primary (75%) + Haiku (25%) | Diversity reduces systematic LLM bias |
| **Parallelism** | Up to 5 concurrent forecasts | Balances throughput vs. API rate limits |
| **Risk halt** | Stop trading at 30% drawdown | Preserves capital for recovery |

---

## Quickstart

### Prerequisites
- Python 3.11+
- API keys: Prophet Arena, Anthropic (Claude), Tavily (optional)

### Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_HANDLE/oracle-prophet-hacks
cd oracle-prophet-hacks

# Copy and fill in your keys
cp .env.example .env
nano .env   # or open in your editor

# Run
./run.sh
```

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `PA_SERVER_API_KEY` | ✅ | Prophet Arena API key — [get it here](https://prophetarena.co/developer) |
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key for Claude |
| `TAVILY_API_KEY` | Recommended | Tavily search API key (free tier available) |
| `OPENAI_API_KEY` | Optional | OpenAI key for ensemble model |
| `ORACLE_SLUG` | Optional | Experiment name (default: `oracle-v1`) |
| `ORACLE_MAX_TICKS` | Optional | Max ticks (default: `96`) |
| `ORACLE_STARTING_CASH` | Optional | Starting cash in dollars (default: `1000.0`) |

### CLI Options

```bash
./run.sh --help

./run.sh --no-search          # Disable web search (faster + cheaper)
./run.sh --ticks 10           # Quick 10-tick smoke test
./run.sh --slug my-run-v2     # Custom experiment name
./run.sh -v                   # Verbose / debug logging
```

### Configuration

Edit `config.yaml` to tune the agent:

```yaml
action:
  min_edge: 0.05        # Only trade when we have ≥5% edge
  kelly_fraction: 0.25  # 25% of full Kelly — conservative
  max_position_notional: 50.0  # Cap per trade at $50

forecast:
  primary_model: "anthropic:claude-sonnet-4-6"
  thinking_budget_tokens: 5000  # Extended thinking for hard questions
```

---

## What Makes ORACLE Different

**1. Superforecaster methodology** — The forecast prompt follows Philip Tetlock's Good Judgment Project framework: reference class → inside view → outside view → market anchor → calibration check. This structured decomposition dramatically reduces anchoring bias.

**2. Kelly Criterion sizing** — Rather than trading fixed sizes, ORACLE uses fractional Kelly to allocate capital proportional to its edge. Markets where ORACLE is only slightly off from the market price get tiny bets; high-conviction divergences get larger positions.

**3. Real-time evidence** — Web search runs per market per tick, so ORACLE's probabilities reflect breaking news, not just training data. Markets that have been moving for good reasons are quickly incorporated.

**4. Adversarial market anchor** — The forecast prompt explicitly tells the model what the market currently believes and asks it to reason about why it might be wrong. This prevents naive anchoring on the market price.

**5. Portfolio-aware risk management** — Global drawdown halt, per-family concentration limits, and cash reserves prevent any single bad run from wiping the account.

---

## Project Structure

```
oracle-prophet-hacks/
├── agent/
│   ├── config.py          # Pydantic config with env var loading
│   ├── loop.py            # Core experiment + tick orchestration
│   ├── main.py            # CLI entry point
│   ├── pipeline/
│   │   ├── review.py      # Stage 1: market selection
│   │   ├── search.py      # Stage 2: web evidence gathering
│   │   ├── forecast.py    # Stage 3: LLM probability estimation
│   │   └── action.py      # Stage 4: Kelly sizing + trade decisions
│   └── utils/
│       ├── llm.py         # Anthropic + OpenAI client wrappers
│       └── search.py      # Tavily + no-op search providers
├── config.yaml            # Default configuration
├── pyproject.toml         # Package metadata + dependencies
├── run.sh                 # Evaluation harness entry point
└── .env.example           # Environment variable template
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

> Built at Prophet Hacks 2026 · University of Chicago

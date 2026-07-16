# Custom Trading Bot

Algorithmic signals + LLM judgment for **risk-gated automated swing trading** (long-only) on a personal brokerage account. A polyglot monorepo split into strict responsibility layers so that trading logic stays testable and every automated order passes through a hard-capped risk gate before it can execute.

Most of this codebase — backend, frontend, agents, and algorithm layers — was built through a **custom-built autonomous coding harness** (see below) rather than one long freeform chat session, which is the part of this project I'm most interested in talking about.

> This is a personal project built to explore safe, testable architecture for systems that touch real money. It is not investment advice, and it does not guarantee profit.

## Harness — a self-driving spec → build → verify pipeline

The bulk of this project wasn't built by prompting an AI coding assistant turn by turn. It was built with **Harness**, a small orchestration framework in this repo (`scripts/execute.py`, `.claude/commands/harness.md`) that turns a large feature into a queue of independent, self-verifying AI coding sessions and runs them unattended:

1. **Spec-first decomposition** — a feature is broken into `phases/{phase}/step{N}.md` files, each scoped to one layer or module, each fully self-contained (no "as discussed earlier" references — every fact a session needs is written into the file), each with a signature-level interface (not implementation) plus explicit non-negotiable rules (idempotency, security, risk hard-caps), and each ending in an **executable acceptance criterion** (e.g. `pytest -q`) instead of a vague description.
2. **Unattended sequential execution** — `python3 scripts/execute.py <phase>` walks the step queue, launching one independent Claude Code session per step.
3. **Guardrail injection** — every session prompt automatically includes the project's `CLAUDE.md` rules and relevant `docs/*.md` architecture decisions, so a session run at 2am with zero human oversight still can't violate the safety invariants (e.g. the risk hard-cap).
4. **Context carry-forward** — each completed step writes a one-line summary of what it built into `phases/{phase}/index.json`; the next step's prompt includes that summary, so later steps know what earlier steps produced without re-reading the whole codebase.
5. **Self-correction loop** — if a step's acceptance criteria fail, the harness retries up to 3 times, feeding the previous attempt's error message back into the next prompt. If a step genuinely needs a human (an API key, an external auth flow, a manual decision), it marks itself `blocked` with a reason and halts cleanly instead of guessing.
6. **Two-stage commits + full audit trail** — each successful step produces a `feat` commit for the code and a separate `chore` commit for its metadata/output, with `started_at`/`completed_at` timestamps recorded automatically — so the entire multi-hour, multi-session build is replayable from git history alone.

In short: instead of babysitting one long AI conversation, I designed the specs and safety constraints up front, then let a fleet of scoped, guardrailed, self-retrying AI sessions build the system phase by phase — with `phases/*/index.json` as the single source of truth for what got built, when, and by which step. `phases/0-foundation` through `phases/3-goal-planner` and `phases/5-momentum-strategy` were built this way end to end.

## What it does

- **3-layer trading pipeline**: rule-based signal generation → filtering → LLM judgment → Kelly-based position sizing, with a risk gate (kill-switch) as the final checkpoint before any order.
- **Goal-based AI personalization**: a user sets a target amount + timeframe; the system deterministically back-solves a risk appetite and position-sizing profile, and an LLM explains the reasoning — the LLM can describe the numbers but never override them.
- **Real-time dashboard**: 6-page Next.js app (portfolio overview, daily/weekly trade logs, AI market direction, goal planner, risk profile) synced over WebSocket.
- **Momentum strategy R&D track**: a separate, report-only research track (time-series momentum + relative strength vs. SPY) backtested and walk-forward validated on historical data — zero live orders, used purely to validate an edge before anything touches a real account.

## Why it's built this way

Real money is involved, so the architecture optimizes for **safety and auditability over speed**:

1. **Multi-stage approval** — every automated order must pass `rule signals → LLM decision → risk gate`, in that order. There is no code path that skips this chain.
2. **Hard-capped risk** — position sizing is computed so that a single trade's max loss can mathematically never exceed `account equity × max risk %`, regardless of input. This cap is enforced in code, not configuration.
3. **LLM boundary** — the LLM is only ever allowed to *explain and judge* (market context, entry/exit rationale, goal-plan reasoning). It never sets or overrides a risk number, position size, or limit — those come from deterministic algorithms only.
4. **Fail-closed** — if a risk check is uncertain or a required signal is missing, the system blocks the trade rather than defaulting to allow.
5. **Kill-switch** — a dedicated risk agent monitors live drawdown and can force-stop every other agent the moment a limit is breached.

## Architecture

A single app isn't enough to keep "trading brain" logic separate from "talks to a broker" I/O, so responsibilities are split into layers:

```
trading_bot/
├── frontend/     # Next.js 14 (App Router) — dashboard, trade logs, AI panels, goal/risk settings
├── backend/      # FastAPI — single gateway for all external APIs, DB access, and secrets
├── agents/       # Scanner / decision / executor / risk / reporter / notifier — independent polling loops
├── algorithms/   # Signals, filters, position sizing, goal-planner math — pure functions, no I/O
├── specs/        # Spec-first definitions of inputs/outputs/edge cases (SDD)
├── tests/        # TDD test suite (Python + Vitest)
├── docs/         # Strategy charter, architecture, ADRs, setup guides
└── phases/       # Staged implementation history for larger workstreams
```

| Layer | Responsibility | Rule |
|---|---|---|
| `algorithms/` | Indicators, sizing, risk math | Pure functions — no I/O, no global state, fully deterministic |
| `agents/` | Scheduled loops + orchestration | All external I/O (broker, LLM, DB) is isolated here |
| `backend/` | External APIs, DB, secrets | The only layer allowed to talk to the broker or the LLM provider (SSOT) |
| `frontend/` | UI rendering + user input | Only ever calls backend REST/WebSocket — never an exchange or LLM directly |

### Trading pipeline

```
watchlist symbol
  │
  ▼ Layer 1 · signal generation      EMA(9/21) cross, RSI(14), MACD histogram → majority vote
  ▼ Layer 2 · filtering (all AND)    volume surge, volatility (ATR), news sentiment, VIX ceiling
  │   — only symbols that pass become candidates —
  ▼ LLM judgment                     synthesizes news + chart context → BUY / HOLD / SELL
  ▼ Layer 3 · position sizing        half-Kelly, ATR-based stop, risk-appetite weighting, hard cap
  ▼ risk gate (kill-switch)          must pass before an order is ever placed
  ▼ execution → fill → report → notification
```

## Tech stack

| Area | Stack |
|---|---|
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind CSS, Recharts, WebSocket |
| Backend | FastAPI, WebSocket, SQLAlchemy, SQLite (dev) / PostgreSQL (prod), Redis |
| AI / LLM | OpenAI API — used for decision rationale, goal-plan explanations, and market summaries only |
| Trading | Robinhood (via MCP server — no public brokerage API exists, so this integrates through a Model Context Protocol server with a safe mock fallback) |
| Algorithms | Python 3.11, Pandas, NumPy, Kelly Criterion (half-Kelly, capped) |
| Strategy R&D | Norgate end-of-day data, custom backtest / walk-forward / out-of-sample harness |

## Development process

Spec-first, test-driven:

1. **Spec** — define inputs/outputs/edge cases in `specs/<feature>.md`
2. **Test (red)** — write a failing test in `tests/test_<feature>.py`
3. **Implement (green)** — minimal code to pass
4. **Refactor** — clean up with tests as a safety net

Enforced by git hooks: pre-commit runs lint/format, pre-push blocks on any failing test. 1,000+ backend tests, 28+ frontend tests.

For large features this spec/test loop is executed *by* the Harness pipeline described above — each `phases/*/step{N}.md` file is itself a spec, and its acceptance criteria are the red→green gate a step must clear before the harness moves on.

## Current status

| Phase | Scope | Status |
|---|---|---|
| 0 · Foundation | Monorepo layout, hooks, FastAPI + WebSocket, 3-layer algorithm core | ✅ Done |
| 1 · Agents | 6 background agents (scanner, decision, executor, risk, reporter, notifier) | ✅ Done |
| 2 · Frontend | 6-page Next.js dashboard | ✅ Done |
| 3 · Goal planner | Goal-based AI personalization | ✅ Done |
| 4 · Live integration | Discord-gated approval workflow, broker order routing, safety hardening | 🚧 In progress |
| 5 · Strategy R&D | Momentum + relative-strength backtesting, walk-forward, OOS validation | 🔄 Ongoing, report-only |

The system currently runs in a safe, mock-isolated mode by default — live order placement is opt-in and gated behind explicit configuration and human approval, never automatic.

## Running locally

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your own API keys — never commit this file
PYTHONPATH=. uvicorn backend.app.main:app --reload   # http://localhost:8000

# Frontend
cd frontend
npm install
npm run dev             # http://localhost:3000

# Tests
python -m pytest -q
cd frontend && npm test
```

No API keys or credentials ship with this repo — `.env` is git-ignored, and every external integration falls back to a safe mock when a key is absent.

## Disclaimer

This is a personal engineering project. It is not financial advice, and no part of it should be interpreted as a recommendation to buy or sell any security. Trading involves risk of loss.

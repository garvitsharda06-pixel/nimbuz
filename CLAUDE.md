# Dalal Desk: rules for Claude Code

Full brief: `docs/BRIEF.md`. Decisions and verified facts: `docs/DECISIONS.md` (keep it current).
Architecture: `docs/ARCHITECTURE.md`. Phase reports: `docs/PHASES.md`.

## Non-negotiables

- **Never fabricate.** No invented market data, backtest results or metrics shown as real. If
  something is unknown, show "—" or "insufficient data". Synthetic data is labelled
  (`provenance="synthetic"`) everywhere, and LIVE mode refuses it.
- **Verify, don't recall.** Broker endpoints, limits, charges and SEBI/NSE rules go in
  DECISIONS.md with source URL and check date.
- **Causality.** Features and setups must use only data available at candle close. Run the
  look-ahead and determinism tests (`make test`) after touching `features/`, `setups/`,
  `backtest/` or `live/`.
- **No order placement** (Phase 7 is gated on explicit owner request).
- **Ask before spending money**: paid data, paid APIs, LLM API calls.
- **Secrets** only in `.env`, never committed or logged. Server binds to 127.0.0.1.
- **Personal use**: Telegram goes only to the owner's chat ID. No broadcasting.
- **Honest board.** Ideas must pass the gate (N≥200 OOS, CI_low > breakeven, conservative EV>0,
  walk-forward stability). Never pad the board.

## Commands

```
make setup      # backend venv + frontend deps
make test       # pytest (incl. look-ahead/determinism) + ruff + mypy
make synth      # generate SYNTHETIC dataset for plumbing/demo (synth-edge: planted edge for known-answer demo)
make backtest   # walk-forward research → statsbook + tearsheets
make replay     # serve API + replay a stored day
make live       # live mode (needs UPSTOX_ANALYTICS_TOKEN)
make ui         # frontend dev server (proxies /api and /ws to :8000)
make e2e        # Playwright smoke tests (after make build-ui)
dalal serve --demo   # gate bypass for watching the plumbing: honoured ONLY on synthetic data
```

## Invariants to keep (tests enforce them)

- Research and live share `dalal/pipeline.py` + `features/` + `setups/`. Replay signals must
  equal research signals (`tests/test_live_engine.py`).
- `live/tracker.py` must reproduce `backtest/simulate.py` exactly (`tests/test_tracker.py`).
- Features use fixed-length windows (no dependence on loaded history length).
- Notes and any prose may contain only numbers present in the facts (`numbers_subset_check`).
- The meta-model ships only if it beats the rule book OOS (Brier ≥1 % better AND higher AUC).

## Style

Python 3.12, type hints, ruff. Pure functions for features/setups. IST tz-aware timestamps
everywhere. Keep modules small; prefer a working slice over a big framework.

# Phase plan and status

The owner asked us to proceed without stopping, so each phase ends with a report section in
`docs/PHASES.md` and a commit. There is no approval gate between phases.

**Status: phases 0–6 done (see docs/PHASES.md). Phase 7 not started, by design.**

| Phase | Scope (this repo) | Acceptance check we can run here | Needs owner's machine |
|---|---|---|---|
| 0 Plan | Verify docs (D-010…D-032), answer §14 with defaults, architecture, CLAUDE.md | Docs present | Confirm D-001 defaults |
| 1 Data & replay | Provider protocol, Upstox adapter (REST + WS + protobuf), instrument master, universe filters, rate-limited downloader, Parquet/DuckDB store, synthetic generator, replay 1–60×, data-quality monitor, minimal price page | Replay a stored (synthetic) day at 30×; quality tests pass | `make download-data` with a real token |
| 2 Research core | Causal features, regime, setups (ORB, VWAP reclaim/reject, trend pullback), backtester with full costs, walk-forward, robustness, tearsheets | Look-ahead and determinism tests; known-answer test (planted edge found, pure noise rejected) | Real verdict on 3 years of real data |
| 3 Scoring & risk | Wilson CI, shrinkage, isotonic calibration, Brier/reliability, conservative EV, gate, board selection, portfolio pass, hysteresis, sizing, guardrails | Unit tests for board logic and calibration | — |
| 4 Live board | Live engine, FastAPI/WS, React terminal UI, drawer with charts, toasts, notifications, kill switch | Replayed day: ideas appear, change status and expire; latency < 1 s measured; Playwright smoke test | Live session test at 09:15 |
| 5 Track record | SQLite journal with snapshots, auto-resolution, paper trades, performance page, Telegram, morning note, EOD report, live-vs-backtest demotion | Journal tests; every shown idea is logged and resolved in replay | Telegram token |
| 6 Depth | Remaining setups (PDH/PDL, gap, RS, VWAP ±2σ MR, OI confirmation), LightGBM meta-model with purged CV and ship gate, OI analytics, sector rotation, overview, strategy lab | Meta-model ships only if it beats the rule book on OOS Brier and EV | Real data |
| 7 Orders | **Not built** (only if explicitly asked) | — | — |

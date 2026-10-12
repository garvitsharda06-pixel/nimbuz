# Phase reports

## Phase 0: Plan (done)

**Built**
- `docs/BRIEF.md`: the brief.
- `docs/DECISIONS.md`: default answers to §14 (D-001), the definitions of probability, ranking
  and gate, and the verified facts with sources and check dates (Upstox endpoints from the
  official SDK source; charges, STT, SEBI algo rules and holidays via web search).
- `docs/ARCHITECTURE.md`, `docs/PLAN.md`, `CLAUDE.md`.

**Hard truths up front**
- The build sandbox has **no access to broker or exchange hosts**. Everything is built and
  tested against a clearly labelled **synthetic** dataset. No real-market edge can be claimed
  from this repo until `make download-data && make backtest` runs on the owner's machine with
  an Upstox token.
- The "official Upstox agent skill packs" mentioned in the brief could not be found (searched
  2026-10-12). None were installed.
- Facts we could only check through search are tagged `VERIFY-ON-FIRST-RUN`.
  `make verify` re-checks the API ones automatically.

**Next**: Phase 1, data layer and replay.

## Phase 1: Data & replay (done)

**Built**
- `MarketDataProvider` protocol (`dalal/data/provider.py`). Providers in place: Upstox, synthetic
  and replay. Angel One, Fyers, Kite and Dhan can be added behind the same interface.
- **Upstox adapter** (`dalal/data/upstox.py`):
  - instrument master parsing (F&O flag, near-month future, indices)
  - V3 1-minute history fetched in monthly chunks
  - intraday candles and the holidays API
  - rate limiter enforcing 25/s, 250/min and 1000/30 min at 80 % of each
  - WebSocket V3 client with reconnect/backoff and **TLS verification on**. The protobuf schema
    was reconstructed from the official SDK descriptor.
- **Store** (`dalal/data/store.py`): one Parquet file per instrument, DuckDB for range and daily
  aggregates, provenance-separated directories.
- **Universe** (`dalal/data/universe.py`):
  - F&O only; 20-day median traded value; price floor
  - ASM/GSM, trade-to-trade, near-circuit, F&O ban (futures shorts) and event-risk exclusions,
    with reasons shown
  - beta vs Nifty; uses only days **before** the as-of date
- **Data quality** (`dalal/data/quality.py`):
  - live `FeedMonitor`: stale > 5 s, clock drift/latency, disconnects and missing bars; fails
    closed
  - offline `check_candles`: OHLC consistency, duplicates, out-of-session bars, missing minutes,
    zero volume, overnight gaps > 30 % (probable unadjusted corporate actions)
- **Replay** (`dalal/data/sources.py`): any stored day at 1–60× (0 = as fast as possible),
  pausable, adjustable speed, emits ticks plus minute batches. `TickMinuteSource` closes live
  minute bars on a wall-clock timer (+300 ms grace).
- **Synthetic generator** (`dalal/data/synthetic.py`): factor model with regimes, volatility
  clustering, gaps, U-shaped vol/volume, and optional planted momentum for known-answer tests.
  Labelled **SYNTHETIC** everywhere.
- **Reference data** (`dalal/data/reference.py`): best-effort, cached and stale-flagged. Covers
  the F&O ban list, sector map (Nifty 500 industries), security-wise bhavcopy (delivery %), and
  owner CSVs for ASM/GSM and events.
- **API**: FastAPI + `/ws` with batched price pushes. Minimal live-price page at `/debug`.
- **CLI**: `dalal synth | check-data | universe | verify | download-data | serve`.

**How to run**

```
make setup && make synth && make check-data && make replay
# open http://127.0.0.1:8000/debug
```

**Tests**: 20 passed. They cover:
- store round-trip and merge, tz enforcement
- resample anchoring and complete-only candles
- tick→bar volume from cumulative vtt
- synthetic determinism and quality
- quality detectors, fail-closed monitor
- universe filters and no-look-ahead
- calendar
- Upstox instrument and candle parsing, month chunks, rate limiter windows, protobuf
  round-trip, reference parsers
- replay order, determinism and 30× pacing
- API replay over WebSocket; LIVE refuses synthetic data

**Acceptance (here)**: replay at 30× in Chromium (headless) advanced about 1 market minute per
2 s, with candles drawing and prices flashing. `check-data` reported **ALL CHECKS PASSED** on 40
stocks + 10 indices × 750 synthetic days.

**Known gaps**
- The Upstox REST and WS paths are tested against recorded formats and our own protobuf
  encoding, not against the live service; run `make verify` on your machine.
- Reference URLs (NSE archives, niftyindices) are unverified from the sandbox; they fail soft.
- Corporate-action adjustment needs `config/reference/corporate_actions.csv`. Until it's filled
  in, unexplained gaps are flagged and excluded.

## Phase 2: Research core (done)

**Built**
- **Day panels** (`features/panel.py`): one symbol's history as days × 375 one-minute bars and
  days × 75 five-minute bars, plus daily OHLCV and traded value. Forming candles are hidden.
- **Causal features** (`features/core.py`):
  - **Daily context, known before the open**: PDH/PDL/PDC, ATR14, σ (20-day), median traded
    value, slot-volume and realised-vol baselines, β (40-day). All are computed over fixed-length
    windows (`sliding_window_view`), so values don't depend on how much history is loaded.
  - **Intraday**: VWAP ±σ from 1-minute bars, EMA9/20 and slope, ATR(5m), 15-min trend filter,
    opening range, running extremes, cumulative and bar RVOL, gap vs ATR, realised vol vs its
    baseline, and RS vs Nifty (β-adjusted) and vs sector in σ units.
- **Regime classifier** (`regime/classifier.py`), every candle:
  - inputs: Nifty vs its session TWAP (indices carry no volume), the 15-min trend, 20-EMA
    alignment, breadth (% of universe above VWAP), India VIX change, and realised vol vs baseline
  - output: TREND_UP / TREND_DOWN / RANGE / HIGH_VOL_CHOP, plus sector strength ranks
- **Setups** (`setups/library.py`). The first 3 the brief asked for, plus the rest of §8.2
  delivered early:
  - ORB
  - VWAP reclaim/rejection
  - trend pullback
  - PDH/PDL break & retest
  - gap-and-go
  - gap-fade
  - relative-strength leader/laggard
  - VWAP ±2σ mean reversion
  - ORB + OI confirmation: **disabled**, needs futures OI history

  Each setup declares its allowed regimes, stop-entry trigger, structural stop with an ATR noise
  floor, T1/T2, time window and invalidation text.
- **Shared pipeline** (`pipeline.py`): one code path for research and live (indices → breadth →
  regime → RS → setup context → feature snapshot).
- **Backtester** (`backtest/engine.py`, `simulate.py`):
  - signals only at 5-min closes; stop-entry fills on 1-minute bars; ideas void if the stop
    trades first
  - same-bar stop+T1 ⇒ stop; limit targets need a tick through; 50 % at T1 then breakeven; 15:15
    time exit
  - spread- and volatility-based slippage
  - risk-based sizing capped by MIS leverage
  - full cost model applied to both legs
- **Cost model** (`backtest/costs.py` + `config/costs.yaml`): brokerage, STT (0.025 % cash /
  0.05 % futures sell side), exchange transaction charge, SEBI fee, stamp duty and GST, each with
  its source and check date.
- **Walk-forward** (`backtest/walkforward.py`):
  - expanding training window ≥ 12 months, 3-month test folds, 2-day embargo
  - OOS probabilities come from regime-bucket hit rates estimated on training data only, shrunk
    toward the setup mean
- **Calibration** (`scoring/calibration.py`): isotonic regression, evaluated forward-chained
  (fit on earlier folds, score on later ones). Reports Brier for base rate vs raw vs calibrated,
  plus reliability tables.
- **Robustness** (`backtest/robustness.py`):
  - Monte Carlo on trade order (drawdown distribution) and bootstrap of total R
  - deflated Sharpe with a persistent **trials ledger** (every variant ever run counts)
  - breakdowns by year, regime, hour and side
  - pre-declared 3×3 sensitivity grids per setup
- **Stats book** (`scoring/statsbook.py`): per setup × side × regime OOS evidence (N, Wilson CI,
  W̄/L̄, expectancy, breakeven, calibrated p, conservative EV, WF stability, DSR), with
  gate pass/fail reasons.
- **Tearsheets** (`backtest/report.py`):
  - OOS and IS side by side, equity in R and ₹, drawdown, WF folds, calibration
  - MAE/MFE histograms, trades per day, exit reasons, Monte Carlo, DSR, sensitivity grid
  - a plain-English verdict
- **CLI**: `dalal backtest [--sensitivity] [--symbols] [--setups]` prints the verdict table and
  writes `data/<provenance>/research/{statsbook.json, instances.parquet, tearsheets/*.json,
  summary.json, trials.json}`.

**Results: SYNTHETIC data only (no real market data was reachable from the build sandbox)**

Null synthetic dataset: 40 stocks × 750 days, a random-walk factor model with **no planted
edge**. The run took 41 s and produced 88k instances, 57k of them OOS. **Every setup came out NO
EDGE or INSUFFICIENT DATA after costs.** OOS expectancy ran from −0.03 R to −0.35 R, and nothing
reached the board. This is the correct result: with no edge in the data, costs and slippage make
every rule lose.

Known-answer test, with idiosyncratic momentum planted in a separate synthetic dataset
(24 × 500 days):

| Setup / side | Verdict | OOS N | Hit rate | OOS expectancy |
|---|---|---|---|---|
| ORB long | **EDGE** | 491 | 68 % | +0.28 R |
| ORB short | **EDGE** | 471 | 65 % | +0.21 R |
| Relative strength long | **EDGE** | 425 | 70 % | +0.45 R |
| Relative strength short | **EDGE** | 350 | 70 % | +0.41 R |
| VWAP mean reversion | **NO EDGE** (correct: momentum hurts mean reversion) | — | — | — |

Brier score was 0.250 for the base rate and 0.230 for the bucket model. **So the engine finds a
planted edge and rejects noise. That tells you nothing about real NSE stocks.** The real verdict
needs `make download-data && make backtest` on the owner's machine.

**Tests** (33 total, all pass):
- **Look-ahead**: perturbing future bars changes no past feature, regime, breadth or signal.
- **Live/research parity**: a live panel with only 25 prior days plus a partial day equals the
  full-history features to within 1e-12.
- **Determinism**: two research runs produce identical instances.
- **Fill rules**: 8 scenarios, plus the short-side mirror.
- **Cost model**: checked against a hand calculation.
- **Sizing**: risk-based, MIS cap, lot rounding.
- **Stats**: Wilson, breakeven, EV.
- **DSR**: more trials give a bigger haircut.
- **Folds**: embargo honoured, no overlap.
- **Walk-forward**: does not leak future regimes.
- **Known-answer**: planted edge found, noise rejected.

`ruff` and `mypy` are clean.

**Known gaps**
- Historical event risk and ASM/GSM membership aren't available historically. The backtest
  universe uses only liquidity and price filters (live applies all filters).
- No pre-open (09:00–09:08) volume in 1-minute candles. Gap setups use first-15-min RVOL as a
  proxy.
- Index "VWAP" is a session TWAP, because indices have no volume.

## Phase 3: Scoring & risk (done)

**Built**
- **Probability**: per setup × side × regime bucket hit rate from OOS instances. A bucket is
  used only when it has ≥ the gate's N (200); otherwise the setup-side pool is used. Values are
  shrunk toward the setup mean (Beta prior) and isotonic-calibrated (`scoring/statsbook.py`,
  `scoring/calibration.py`).
- **CI and ranking**: Wilson 95 % CI, shifted with the calibration. Conservative EV =
  p_lo·W̄ + (1−p_lo)·L̄. Breakeven p = −L̄/(W̄−L̄).
- **Gate** (configurable), with the failing reasons shown:
  - N ≥ 200 OOS
  - CI low > breakeven
  - conservative EV > 0
  - walk-forward stability ≥ 60 %
- **Reliability diagram and Brier score on held-out data**: forward-chained (isotonic fit on
  earlier folds, scored on later folds). Printed by `dalal backtest`, stored in `summary.json`,
  rendered in the Strategy Lab.
- **Board selection** (`scoring/board.py`, pure and unit-tested):
  - one idea per symbol (the best conservative EV)
  - top 5 per side; **never padded**
  - ≤ 2 per sector per side
  - near-duplicates dropped (20-day return correlation > 0.8)
  - net-beta cap on notional/capital
  - max open ideas
  - hysteresis: a WATCHING idea is displaced only by a candidate with ≥ 20 % higher conservative
    EV that also fits the portfolio. TRIGGERED ideas are never displaced.
- **Candidate builder** (`scoring/candidates.py`):
  - stats-book lookup; gate enforcement
  - the stats book's provenance must match the data's (real stats never mix with synthetic
    data, or the reverse)
  - the demo gate bypass is honoured **only** on synthetic data
  - demoted setups are refused
  - sizing, entry zone, validity window, grade (A+/A/B)
- **Sizing** (`risk/sizing.py`): (capital × risk %) ÷ |entry − stop|, rounded to lots for
  futures shorts, capped by MIS notional (capital × leverage).
- **Guardrails** (`risk/guardrails.py`): kill switch, feed health, before 09:20, after 14:45,
  daily loss limit, max open ideas, market-wide event blackouts (calendar.yaml).
- **Thesis bullets** (`scoring/explain.py`): a setup-specific primary bullet plus the top drivers
  by salience (RS vs Nifty/sector σ, sector rank, RVOL, breadth, VWAP distance, 15-min trend,
  gap, futures OI). Built only from computed numbers.

**Synthetic run with sensitivity grids** (72 variants in one pass, 179 s): the null dataset is
still NO EDGE everywhere. The trials ledger now counts every variant, and the deflated Sharpe
haircut uses it.

**Tests**: 42 pass. The 9 new board/risk tests cover never-pad, top 5 by conservative EV,
sector cap, correlation dedupe, beta cap, hysteresis, no displacement of triggered ideas, one
idea per symbol, gate + synthetic-only bypass + provenance mismatch, sizing, guardrails and the
thesis text.

## Phase 4: Live board v1 (done)

**Built**
- **Live engine** (`live/engine.py`). LIVE and REPLAY use the same code. On every completed
  5-min candle, the universe is processed as **one stacked batch**: prev-day and today rows for
  all symbols go through the *same* feature, regime and setup functions the research engine uses.
  The rest of the pipeline is breadth, then regime, then setups, then the stats-book gate,
  sizing, guardrails, and the board (portfolio pass + hysteresis). Ideas are journaled through a
  sink interface (Phase 5).
- **Tracker** (`live/tracker.py`):
  - status machine Watching → Triggered → T1 hit → T2 hit / Stopped / Closed (breakeven or
    15:15) / Expired / Invalidated / Displaced
  - same rules as the backtester; test: 40 random paths give identical fills, labels and net R
  - LIVE also tracks on ticks
- **API**: `/api/board`, `/api/market`, `/api/idea/{id}` (card, evidence, 5 most similar
  **earlier** OOS instances), `/api/research/*`, `/api/settings`. `/ws` streams status, health,
  prices (batched every 250 ms), minute, board, market, idea_new, idea_update. JSON encoding is
  numpy-safe.
- **Terminal UI** (`frontend/`: React + TS + Vite + Tailwind, Lightweight Charts, ECharts,
  Zustand, TanStack Query; Inter + JetBrains Mono with tabular numbers, bundled locally):
  - **Top bar**: IST clock, market status, Nifty, Bank Nifty, VIX, USD/INR ("—" when there's no
    source), A/D, % above VWAP, regime badge, feed-health dot + latency, LIVE/REPLAY + SYNTHETIC
    badges, browser-alert enable, mute, **kill switch**, replay controls (day, speed, pause).
    Red banner when the feed is stale.
  - **Board**: TOP LONGS | TOP SHORTS. Each card shows symbol, sector, setup, grade, entry zone,
    stop, T1/T2, R:R, calibrated P with Wilson CI and N, conservative EV, expectancy, qty and
    ₹ risk, signal time and validity, live distance to entry, status, a sparkline with VWAP,
    3 thesis bullets and the invalidation. Also: a never-padded empty state, guardrail and
    filter counts, a resolved-today table, and the stats-book provenance.
  - **Demo ideas and synthetic data are always labelled**: a striped "GATE BYPASSED: PLUMBING
    DEMO" ribbon and a SYNTHETIC watermark. An **in-sample warning** appears when a replay day is
    inside the stats book's sample.
  - **Pop-ups**: slide-in toasts with a glow, distinct WebAudio tones for long and short, and
    browser notifications when the tab is hidden.
  - **Drawer**:
    - Lightweight Charts candles (1/5/15-min) with VWAP, EMA20, volume and entry/stop/T1/T2
      lines; TradingView attribution logo + link
    - plan, full evidence panel (calibrated P, CI, N, W̄/L̄, expectancy, breakeven, conservative
      EV, PF, WF stability, DSR, live hit), thesis, timeline, similar instances, feature
      snapshot
    - futures OI shows "not available" (honest)
  - **Market overview**: sector heatmap (treemap), sector-rotation chart vs Nifty, breadth
    curve, sector ranking, volume shockers, RS leaders and laggards, and honest empty OI and
    events panels.
  - **Keyboard**: J/K move, Enter open, Esc close, / search, Space mute.
  - **Mobile**: Longs/Shorts tabs with swipe.
- `dalal serve --demo`: gate bypass so you can watch the plumbing. **Honoured only on synthetic
  data.**

**Measured**
- 5-min pipeline: **28–94 ms** for the 31-symbol synthetic universe.
- Scale test with **453 symbols**: **145 ms median, 180 ms p95, 521 ms max**. All within the 1 s
  budget.

**Tests**: 86 backend tests pass, including:
- **live/replay ↔ research signal parity** on a replayed day (every raw signal identical)
- ideas appear, update and fully resolve by 15:15
- pipeline < 1 s
- nothing shown when nothing qualifies and the bypass is off
- tracker ≡ simulator (40 paths)

2 Playwright smoke tests pass: board, keyboard, drawer + chart + evidence, market charts, kill
switch, mobile tabs.

**Bugs found and fixed during visual QA**:
- numpy values silently killed the WebSocket
- the chart crashed on unmount
- the BANK sector index never received bars (shared key with Bank Nifty)
- "similar instances" leaked the replay day
- the notional-based beta cap allowed only one or two longs; it's now risk units, cap 5

**How to run**

```
make synth && make backtest && make build-ui
backend/.venv/bin/dalal serve --demo        # open http://127.0.0.1:8000, pick a day, ▶ Start
```

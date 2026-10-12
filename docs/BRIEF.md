# Build Brief: NSE Intraday Trading Desk ("Dalal Desk")

> This is the owner's original brief, kept as written. Answers and verified facts are in
> `docs/DECISIONS.md`.

You are building a personal, institutional-grade trading terminal for Indian equities. Read this entire brief before doing anything. Work as a combined team: a senior quant researcher from a top-tier equity desk, a senior Python/TypeScript engineer, and a product designer who has shipped professional trading terminals.

## 1. Mission

During NSE market hours, continuously scan a liquid universe and pop up the top 5 long and top 5 short intraday trade ideas with the highest measured probability of success. Every idea comes with entry, stop, targets, position size, a chart and a plain-English thesis — research quality and transparency on par with a BlackRock or Morgan Stanley desk, and a dashboard that looks and feels like a modern Bloomberg terminal.

- Scope: research engine, live signals, paper trading, journal and track record.
- Human-in-the-loop: no automatic order placement (optional, gated Phase 7 only).
- Personal use only: signals are never broadcast to other people — sharing buy/sell calls requires SEBI Research Analyst registration.

## 2. What "institutional grade" means here

- Rigor — every setup is validated out-of-sample with realistic costs before it can reach the live board.
- Honest probabilities — numbers on screen are measured and calibrated, never guessed or hard-coded.
- Risk first — sizing, limits, diversification and event blackouts are built in, not bolted on.
- Explainability — every idea shows why (its drivers) and what kills it (invalidation).
- Accountability — every idea ever shown is logged and scored, so the terminal proves or disproves itself.
- Reliability — if data is stale or broken, the terminal says so and stops signalling.

## 3. Working rules for you (Claude Code)

- Plan first. Read this brief, ask me the questions in §14, then propose the architecture, repo layout and phase plan. Write no code until I approve.
- Build in phases (§13). End each phase with: what was built, how to run it, test results, known gaps, next step. Commit after each phase with clear messages.
- Verify, don't recall. For broker API endpoints, auth flows, rate/subscription limits, transaction charges, and SEBI/NSE rules, read the current official docs and record the source URL and date checked in docs/DECISIONS.md.
- Never fabricate. No invented data, backtest results or "sample" metrics shown as real. Unknown = "—" or "insufficient data".
- Tell me hard truths. If a setup shows no edge after costs, say so plainly and keep it off the live board. Two honest ideas beat five weak ones.
- Ask before spending money (paid data, paid APIs, LLM API calls).
- Keep it simple and working. Prefer a small working vertical slice over a large unfinished framework.
- Create a concise CLAUDE.md (key rules + pointer to this brief) and keep docs/DECISIONS.md current.

## 4. My setup (confirm with me in Phase 0)

- Market: NSE cash equities, intraday, 5- and 15-minute candles.
- Data feed: I had planned a free delayed feed — this must change. Live signals need real-time data (see §6).
- Broker / API: [____]
- Capital: ₹[____] | Risk per trade: [0.5]% | Max daily loss: [2]%
- Shorts via: [intraday cash (MIS) / stock futures / both]
- Machine: [Windows / Mac]; runs locally; phone access over a private network is a bonus.
- Alerts: in-app + browser notifications + Telegram to my own chat only.

## 5. "Probability" — one precise definition

Probability = P(price reaches Target 1 before the stop-loss, before the 15:15 IST cutoff), estimated from out-of-sample historical instances of the same setup in similar conditions, calibrated, with costs included in the outcome.

Every trade card shows: calibrated probability, sample size N, 95% confidence interval (Wilson), expectancy in R after costs, and — once it exists — the setup's live forward hit rate.

- Ranking key = conservative EV: expectancy after costs computed with the lower bound of the probability CI. This favours ideas with strong evidence over lucky small samples, and a 55% idea paying 2R over a 70% idea paying 0.3R.
- Minimum bar for the live board (configurable): N ≥ 200 out-of-sample instances; CI lower bound above the breakeven win rate for that reward:risk; positive conservative EV; stable across walk-forward windows.
- If fewer than 5 ideas qualify on a side, show fewer. Never pad the board.
- Calibration is checked with a reliability diagram and Brier score on held-out data, then continuously against live outcomes (§10).

## 6. Data layer

- Provider-agnostic adapter (MarketDataProvider): instruments, historical candles, live ticks over WebSocket, quotes/depth, futures OI where available. Use each broker's official SDK.
- Recommended default: Upstox API with its free, read-only Analytics Token (long-lived, no daily login) for real-time WebSocket market data — check which API categories (e.g., historical candles) it covers. Alternatives behind the same interface: Angel One SmartAPI or Fyers (data free with an account), Zerodha Kite Connect or Dhan (paid data). Upstox publishes official agent skill packs for its APIs — install them if we go with Upstox.
- No live signals on delayed data. A 15-minute-delayed feed makes 5-minute signals untradeable. Delayed/EOD sources are acceptable only for research backfill.
- Replay provider: replays any stored day at 1×–60× through the exact same pipeline, selectable from the UI. This is how we develop after hours, test and demo.
- Historical store: ≥ [3] years of 1-minute candles for the universe plus Nifty 50, Bank Nifty, sector indices and India VIX, downloaded within API rate limits, stored as Parquet, queried with DuckDB. Adjust for splits/bonuses; document survivorship-bias gaps.
- Reference data (daily, cached, best-effort, never blocking): F&O ban list, ASM/GSM lists, NSE bhavcopy (delivery %), corporate announcements and results/board-meeting calendar (official exchange feeds), FII/DII provisional flows, trading-holiday calendar, overnight global cues (US index futures, crude, USD/INR; GIFT Nifty only if a reliable, permitted source exists). Use official sources with polite rate limits; never scrape against a site's terms. If a source fails, mark that panel stale and carry on.
- Data-quality monitor: stale ticks, missing candles, clock drift, disconnects. If live data is stale > [5] s during market hours, suspend new signals and show a red banner; reconnect with backoff.

## 7. Universe and session rules

- Universe: NSE F&O stocks (liquid and shortable), rebuilt daily from the instrument master; optionally Nifty 500 names that pass the filters.
- Filters: 20-day median traded value ≥ ₹[50] cr; price ≥ ₹[50]; exclude ASM/GSM, trade-to-trade series, stocks pinned near circuit/price-band limits, and F&O-ban stocks for futures shorts. Tag "event risk" on results / board-meeting / major-announcement days (excluded by default).
- Session (IST, from config): signals from 09:20 (setups that need an opening range wait for it); no new ideas after 14:45; open ideas expire at 15:10; outcomes resolved at 15:15. Handle holidays, special sessions and Muhurat trading from an official calendar. All timestamps tz-aware, Asia/Kolkata.
- Timeframes: signals on completed 5-minute candles, 15-minute trend filter, ticks for live trigger/stop tracking, 1-minute bars for fill simulation in backtests.

## 8. Research engine

### 8.1 Market regime (updated every candle)

Nifty and Bank Nifty structure (VWAP, opening range, EMA alignment), breadth (advance/decline, % of universe above VWAP), India VIX level and change, gap type, sector strength ranking, intraday realised volatility. Classify: Trend Up / Trend Down / Range / High-Vol Chop. Each setup declares the regimes it is allowed to fire in.

### 8.2 Setup library

Each setup is an independent, separately backtested module that declares its timeframe, allowed regimes, entry trigger, structural stop (swing- or ATR-based), T1/T2, max holding time and invalidation rules. Start with:

- Opening-range breakout / breakdown (15-min range) with relative-volume confirmation
- VWAP reclaim / VWAP rejection aligned with the index trend
- Trend pullback to the 20-EMA or VWAP on rising volume
- Previous-day high/low break and retest
- Gap-and-go vs gap-fade (gap size vs ATR, pre-open volume)
- Relative strength vs Nifty and sector — leaders for longs, laggards for shorts, in trend regimes
- VWAP ±2σ mean reversion — range regime only
- F&O positioning as confirmation: futures price × OI change → long build-up / short build-up / short covering / long unwinding

Add more setups only if they pass §9.

### 8.3 Scoring, calibration and ranking

- Labels: triple-barrier (T1 / stop / time-out at 15:15).
- Step 1: rule-based setups with per-setup statistics bucketed by regime.
- Step 2: a LightGBM meta-model that predicts which setup instances succeed (meta-labelling), trained with purged, embargoed walk-forward CV and calibrated with isotonic regression on a held-out fold. It ships only if it beats Step 1 out-of-sample.
- Explainability: the top 3–5 feature contributions (e.g., SHAP) rendered as thesis bullets — "RS vs Nifty +1.8σ", "Sector rank 1/15", "Futures short build-up: OI +12%, price −1.4%".
- Portfolio pass: max 2 ideas per sector per side; drop near-duplicates (20-day return correlation > 0.8); cap the net beta of the board.
- Stability: re-rank every candle with hysteresis — an idea stays until it is invalidated, expires, or is beaten by a candidate with ≥ [20]% higher conservative EV. No flicker.

## 9. Backtesting and validation (non-negotiable)

- Event-driven backtester: signals use only data available at candle close; fills at the next bar with a spread- and volatility-based slippage model; intraday square-off rules.
- Full Indian cost model from config, separately for cash intraday and futures: brokerage, STT (intraday equity 0.025% on the sell side; futures 0.05% on the sell side since 1 Apr 2026), exchange transaction charges, SEBI fee, stamp duty, GST. Verify every rate against official/broker sources and record the date checked.
- Walk-forward out-of-sample testing; show in-sample vs OOS side by side.
- Robustness: parameter-sensitivity heatmaps (prefer plateaus to peaks), Monte Carlo on trade order, breakdowns by year, regime, time of day and long vs short. Count every variant tried and apply a deflated-Sharpe / multiple-testing haircut.
- Automated look-ahead tests: perturbing future bars must never change past signals. Determinism test: replaying the same day twice yields identical signals.
- Tearsheet per setup: equity curve (R and ₹), drawdown, hit rate, expectancy, profit factor, average win/loss, MAE/MFE, trades per day, calibration plot.

## 10. Risk, journal and live track record

- Position size = (capital × risk %) ÷ (entry − stop), rounded to quantity/lot rules and within broker MIS margin. Every card shows quantity and ₹ at risk.
- Guardrails: max daily loss, max open ideas, per-sector cap, no new ideas after 14:45 or once the loss limit is hit. Kill switch in the UI.
- Event blackouts (configurable calendar): RBI policy, Union Budget, the stock's own results, index rebalancing.
- Journal: every idea ever shown is stored with its full snapshot (features, model version, config hash, timestamp) and auto-resolved with live data (T1 / T2 / stop / expired) — whether I took it or not. Paper trading via one-click "take trade"; manual entries allowed.
- Track record: forward hit rate vs predicted probability (reliability diagram), expectancy by setup / regime / time of day, cumulative R, drawdown. Auto-flag and demote any setup whose live results diverge from its backtest.

## 11. Dashboard — Bloomberg-grade information, modern polish

Design language: dark terminal theme (near-black background, slightly lighter panels, hairline borders); green = long, red = short, amber = warning; Inter for UI and a monospaced face with tabular figures for every number; dense but uncluttered; no gimmicky gradients. Subtle motion only: new cards slide in with a brief glow, price changes flash briefly. Keyboard-first (J/K to move, Enter to open, / to search, Space to mute alerts). Responsive: on mobile, Longs and Shorts become swipeable tabs.

Screens

- Live Board (home)
  - Top bar: IST clock and market status, Nifty 50, Bank Nifty, India VIX, USD/INR (if available), advance/decline, regime badge, feed-health dot with latency, LIVE/REPLAY indicator, kill switch.
  - Two columns — TOP 5 LONGS | TOP 5 SHORTS. Each card: symbol, sector, setup, conviction grade (A+/A/B), entry zone, stop, T1/T2, reward:risk, calibrated probability with CI and N, conservative EV (R), quantity and ₹ risk, signal time and validity window, live distance to entry, status (Watching → Triggered → T1 hit / Stopped / Expired), intraday sparkline with VWAP, 3 thesis bullets and the invalidation condition.
  - Pop-ups: new ideas slide in as toasts (distinct sounds for long and short), trigger browser notifications when the tab is hidden, and go to Telegram.
- Trade detail drawer: TradingView Lightweight Charts candles (1/5/15-min) with entry/stop/targets drawn, VWAP, EMAs, volume and futures OI; the setup's statistics and the 5 most similar past instances with outcomes. Keep the attribution its licence requires.
- Market overview: sector heatmap/treemap, sector relative-rotation chart vs Nifty, breadth, volume shockers, F&O OI build-up table, corporate-announcements feed, today's events.
- Morning note (auto, 08:45 IST): overnight global cues, key index levels, FII/DII flows, events, expected regime and watchlist — written like a sell-side morning note. If an LLM writes the prose, it may only rephrase numbers the system computed; it must never introduce a number.
- End-of-day report (15:45 IST): what fired, outcomes, calibration update.
- Performance & journal (§10).
- Strategy lab: tearsheets, walk-forward charts, parameter heatmaps, enable/disable setups.
- Settings: capital, risk, filters, thresholds, alert channels, API status, replay date and speed.

Performance targets: an idea reaches the UI within 1 s of the candle close that triggers it; the UI stays smooth with 500+ subscribed symbols.

## 12. Tech stack (propose changes only with strong reasons)

- Backend: Python 3.12, FastAPI + WebSockets, asyncio; polars/pandas, numpy; LightGBM, scikit-learn (calibration), SHAP; DuckDB + Parquet for market data; SQLite for the journal (Postgres later if needed); APScheduler; pydantic-settings; structured logging.
- Frontend: React + TypeScript + Vite, Tailwind + shadcn/ui, TradingView Lightweight Charts, ECharts (heatmaps/treemaps), TanStack Query, Zustand.
- Quality: pytest (including look-ahead and determinism tests), ruff, pyright/mypy, Playwright smoke tests, pre-commit hooks.
- Ops: Docker Compose (one command to run everything); .env for secrets, never committed or logged; server bound to localhost by default (phone access via Tailscale, never the open internet); README; Makefile tasks download-data, backtest, replay, live.
- Repo layout: backend/{data,features,regime,setups,scoring,risk,backtest,live,journal,api,notify}, frontend/, research/, config/, docs/.

## 13. Build phases and acceptance criteria

| Phase | Build | Done when |
|---|---|---|
| 0 — Plan | Ask §14 questions; verify broker docs, limits and charges; propose architecture, repo and plan | I approve the plan |
| 1 — Data & replay | Adapter, instrument master, universe filters, historical download, replay engine, minimal live-price page | I can replay any stored day at 30× and watch candles flow; data-quality checks pass |
| 2 — Research core | Features, regime classifier, first 3 setups, backtester with full costs, tearsheets | Walk-forward OOS results over ≥ 2–3 years; look-ahead tests pass; honest verdict on which setups have an edge |
| 3 — Scoring & risk | Calibration, conservative-EV ranking, portfolio pass, position sizing | Reliability diagram + Brier score on held-out data; board-selection logic unit-tested |
| 4 — Live board v1 | Live engine, API/WebSocket, Live Board, detail drawer, pop-ups | On a replayed day, ideas appear, update status and expire correctly within 1 s, and it looks like a professional terminal |
| 5 — Track record & alerts | Journal, paper trading, performance page, Telegram, morning note, EOD report | Every shown idea is logged and auto-resolved; calibration updates from live data |
| 6 — Depth | Remaining setups, LightGBM meta-model, OI analytics, sector rotation, polish | Each addition beats the previous version out-of-sample, or it doesn't ship |
| 7 — Orders (only if I explicitly ask) | Confirm-before-send orders with broker-side stop-loss | Complies with SEBI's retail algo framework (fully in force since 1 Apr 2026): whitelisted static IP for API orders, daily 2FA login, below the 10 orders/sec threshold, plus my broker's current rules — all verified first |

## 14. Ask me these before you start

1. Which broker account(s) do I have — can I generate an Upstox Analytics Token, or which alternative do we use?
2. Capital, risk per trade and max daily loss?
3. Shorts via MIS cash, stock futures, or both? (Futures STT is now 0.05% vs 0.025% for intraday cash.)
4. How much 1-minute history can my API provide, and is a paid data vendor acceptable if it's not enough?
5. Telegram alerts — yes or no?
6. Any existing code, strategies or notes to reuse?
7. OS, and is Docker installed?

## 15. Definition of done

On any trading day I open the terminal at 09:10 and see the morning note and the day's regime. From 09:20 there is a stable live board of at most 5 longs and 5 shorts, each with calibrated probability, N, confidence interval, expectancy after costs, position size, chart and plain-English thesis; new ideas pop up with alerts; and every idea is auto-tracked, so after a month I can see whether the probabilities were honest.

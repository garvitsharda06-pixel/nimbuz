# Architecture

```
               ┌──────────────── MarketDataProvider (protocol) ────────────────┐
               │ UpstoxProvider   ReplayProvider(store, day, speed)  Synthetic │
               └─────┬───────────────────────┬─────────────────────────────────┘
      ticks / 1m bars│                       │ historical 1m candles
                     ▼                       ▼
 ┌────────────── live/engine.py ────────┐   data/store.py  (Parquet + DuckDB)
 │ BarBuilder (tick→1m→5m/15m)          │        ▲
 │ DataQualityMonitor (stale/gaps/drift)│        │ download.py (rate-limited)
 │ on 5m close:                         │
 │   features.compute(day-so-far) ──────┼── same pure functions used by backtest
 │   regime.classify                    │
 │   setups.evaluate → candidates       │
 │   scoring.statsbook → p, CI, EV      │
 │   risk.size + guardrails + blackouts │
 │   scoring.board (portfolio+hysteresis)
 │ on 1m bar / tick: tracker (status)   │
 │ journal.log every shown idea         │
 └──────┬───────────────────────────────┘
        │ events (pub/sub bus)
        ▼
 api/app.py  FastAPI REST + /ws  ──►  frontend (React/Vite)  ──► browser notif, sounds
        │                                   Live Board · Drawer · Overview · Performance · Lab · Settings
        └──► notify/telegram.py (own chat only)

 research path (offline):
   download → store → backtest/engine (event-driven on 1m) → walkforward → statsbook.json
   → calibration (isotonic, Brier, reliability) → tearsheets → Strategy Lab
   → metamodel (LightGBM, purged CV)  ships only if it beats the rule-based book OOS
```

## Repo layout

```
backend/
  dalal/
    config.py          pydantic-settings + YAML loader, config hash
    clock.py           IST helpers, session calendar (holidays, special sessions)
    log.py             structured logging (secrets redacted)
    data/              models, provider protocol, upstox, replay, synthetic, store,
                       universe, quality, reference, download, candles
    pipeline.py        the ONE signal pipeline shared by research and live (indices → breadth → regime → RS → setups)
    features/          day panels; causal indicators (VWAP, σ-bands, EMA, ATR, RVOL, OR, PDH/PDL, RS, gap); futures OI
    regime/            index + breadth + VIX regime classifier
    setups/            one module per setup + registry
    backtest/          engine (triple barrier on 1m), costs, slippage, walk-forward,
                       robustness (sensitivity, Monte Carlo, deflated Sharpe), tearsheet
    scoring/           stats (Wilson, EV), calibration, statsbook, board (portfolio,
                       hysteresis), candidates, explain (thesis bullets), metamodel (LightGBM + TreeSHAP), similar
    risk/              sizing, guardrails, blackouts
    live/              engine (stacked per-candle batch), tracker (≡ backtester rules), runtime, services, bus
    journal/           SQLite store, resolver, performance analytics
    notify/            telegram, morning note, EOD report
    api/               FastAPI app + WebSocket
    cli.py             `dalal` CLI (verify, synth, download-data, backtest, replay, live, serve)
  tests/
frontend/              React + TS + Vite + Tailwind; Lightweight Charts; ECharts; Zustand; TanStack Query
config/                settings.yaml, costs.yaml, calendar.yaml, reference/*.csv
research/              notes and output of research runs (generated, git-ignored)
docs/                  BRIEF, DECISIONS, ARCHITECTURE, PLAN, PHASE reports
```

## Key invariants

1. **Causality**: every feature row *t* depends only on rows ≤ *t*. Tests prove it.
2. **One pipeline**: live and replay share the same engine code. The backtester calls the same
   feature, setup and tracker functions.
3. **Provenance**: synthetic data cannot drive LIVE mode, and anything derived from it is
   watermarked.
4. **Fail closed**: stale feed, clock drift, kill switch or daily-loss limit means **no new
   ideas**. Existing ideas are still tracked.
5. **Secrets**: only in `.env`. Never logged, because the logger redacts any key containing
   token, secret or password.
6. **Bound to 127.0.0.1** by default. To reach it from your phone, use Tailscale (README).

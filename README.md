# Dalal Desk

A personal NSE intraday research and signal terminal. It scans a liquid F&O universe on every
completed 5-minute candle and shows **at most 5 long and 5 short ideas** that pass an
out-of-sample evidence gate. Each idea comes with:
- a calibrated probability, its sample size and a confidence interval
- expectancy after Indian costs, and the conservative EV it is ranked by
- position size and rupee risk
- a chart, a plain-English thesis and its invalidation

Every idea shown is journaled and scored, so the terminal proves or disproves itself.

> **Places no orders.** Human-in-the-loop only. **Personal use only:** sharing buy/sell calls
> requires SEBI Research Analyst registration, so Telegram alerts go only to your own chat.

## Hard truths first

- **No real-market verdict exists yet.** This repo was built in a sandbox with no access to
  broker or exchange data. All results shown so far come from a clearly labelled **synthetic**
  dataset, used to build and test the plumbing.
  - On the null synthetic dataset, **every setup is correctly rejected** (no edge after costs).
  - On a dataset with a planted edge, the engine finds it, and real (non-bypassed) ideas reach
    the board on out-of-sample days.
  - The verdict on real NSE stocks comes from `make download-data && make backtest` on your
    machine.
- **The board is never padded.** If nothing passes the gate, it stays empty and says why. The
  gate is: N ≥ 200 OOS instances, Wilson CI low > breakeven, conservative EV > 0, and
  walk-forward stability ≥ 60 %.
- **"Probability"** means P(T1 before stop before 15:15). It is measured on out-of-sample
  instances, calibrated, with costs included. Read §5 of `docs/BRIEF.md`.

## Run it on your own computer

You need **git**, **Node.js 18+** (only to build the UI once) and either
[uv](https://docs.astral.sh/uv/) (recommended; it fetches Python 3.12 for you) or Python 3.12.

```bash
git clone https://github.com/garvitsharda06-pixel/nimbuz.git
cd nimbuz
git checkout claude/nse-intraday-trading-desk-pnuqhf
```

Then start it on the port you want:

| System | Command | Open |
|---|---|---|
| macOS / Linux / WSL | `./scripts/start.sh 8000` | http://127.0.0.1:8000 |
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -Port 8000` | http://127.0.0.1:8000 |
| with make | `make start PORT=8000` | http://127.0.0.1:8000 |

- Any free port works, for example `./scripts/start.sh 8080` or `-Port 8080`.
- If the port is taken, the script stops and tells you.
- The first run installs everything, builds the UI and generates the **synthetic** demo
  dataset. It then runs the research once, which takes a few minutes. Later runs start in
  seconds.
- In the browser, pick a day in the Replay strip and press **▶ Start**. On synthetic noise the
  board correctly stays empty, because nothing passes the gate.
- To watch cards flow anyway, add `--demo`: `./scripts/start.sh 8000 --demo`, or
  `-Port 8000 -Demo` on Windows, or `make start PORT=8000 ARGS=--demo`. Every bypassed card is
  marked "GATE BYPASSED".
- Press Ctrl+C to stop.
- The server listens on 127.0.0.1 only, so only your computer can reach it. For your phone,
  see [Phone access](#phone-access).

## Quick start, step by step (synthetic demo)

```bash
make setup                 # Python 3.12 venv (uv) + frontend deps
make synth                 # SYNTHETIC dataset (labelled everywhere, never market data)
make backtest              # walk-forward research → stats book, calibration, tearsheets, meta-model
make build-ui
backend/.venv/bin/dalal serve --demo     # http://127.0.0.1:8000 → pick a day → ▶ Start
```

`--demo` bypasses the gate so you can watch cards flow. It is honoured **only on synthetic
data**, and every bypassed card carries a striped "GATE BYPASSED: PLUMBING DEMO" ribbon. To see
the honest path, where gate-qualified ideas appear, plant an edge in the synthetic data:

```bash
backend/.venv/bin/dalal synth --momentum 0.6 --days 500
backend/.venv/bin/dalal backtest --end <a date ~2 weeks before the last day>
backend/.venv/bin/dalal serve        # replay a day after --end: ideas that pass the gate, no bypass
```

Docker alternative: `docker compose up --build`. It is published on 127.0.0.1:8000 only.

## Real data (Upstox)

1. Generate an Upstox **Analytics Token** (read-only, long-lived) and put it in `.env`. See
   `.env.example`.
2. Set `data.provider: upstox` in `config/settings.yaml`, and set your `risk.capital`, which is a
   placeholder.
3. `make verify`: probes the API with your token and writes `data/verify_report.json`. Also
   check off the `VERIFY-ON-FIRST-RUN` items in `docs/DECISIONS.md` (charges, limits, token
   scope).
4. `make download-data`: ~3 years of 1-minute candles for the F&O universe and indices. It's
   incremental and rate-limited (25/s, 250/min, 1000/30 min at 80 %). Expect a few hours the
   first time.
5. `make check-data && make backtest` (add `--sensitivity` for parameter grids).
6. `make live` on trading days (or `dalal serve`, then Settings → Start LIVE).

Live signals are refused on synthetic or delayed data. If the feed goes stale for more than
5 s, new signals stop and a red banner appears.

## Screens

| Screen | What it shows |
|---|---|
| **Board** | IST clock, indices, VIX, breadth, regime, feed health, LIVE/REPLAY, kill switch; TOP LONGS / TOP SHORTS cards; resolved-today table; guardrails and filter counts. Keys: `J`/`K` move, `Enter` open, `/` search, `Space` mute. On mobile, longs and shorts are swipe tabs. |
| **Drawer** | Lightweight Charts candles (1/5/15 min) with VWAP, EMA20, volume, entry/stop/T1/T2; full evidence panel; thesis and timeline; 5 most similar earlier OOS instances; feature snapshot; "Take paper trade". |
| **Market** | Sector heatmap, intraday and daily sector rotation, breadth curve, sector ranking, volume shockers, RS leaders/laggards, futures OI build-up. |
| **Performance** | Forward hit rate vs predicted (reliability, Brier), cumulative R, drawdown, expectancy by setup/regime/time, live-vs-backtest demotions, journal, paper trades, manual entries. |
| **Lab** | Verdicts per setup and side; tearsheets (IS vs OOS, equity in R and ₹, walk-forward folds, calibration, MAE/MFE, sensitivity heatmap, DSR, Monte Carlo); meta-model ship decision; enable/disable setups. |
| **Notes** | Morning note (08:45) and end-of-day report (15:45), written only from computed numbers. |

## Commands

```
make start PORT=8000 [ARGS=--demo]   # install on first run, then serve on that port
make test          # pytest (look-ahead, parity, determinism, known-answer…) + ruff + mypy
make e2e           # Playwright smoke tests (needs `make build-ui`; starts the server itself)
dalal backtest [--sensitivity] [--setups orb,vwap_reclaim] [--start] [--end]
dalal universe | check-data | verify | download-data | synth | serve [--port 8000 --replay --speed 30 --demo]
```

## Phone access

The server binds to `127.0.0.1`. To use it from your phone:
1. Install [Tailscale](https://tailscale.com) on both devices.
2. Run `tailscale serve 8000` on the desktop (use the port you started on).
3. Open the tailnet URL on your phone.

Never expose the port to the open internet.

## Documentation

- `docs/BRIEF.md`: the original brief
- `docs/DECISIONS.md`: every decision and verified fact, with source and check date
- `docs/ARCHITECTURE.md`: data flow and repo layout
- `docs/PHASES.md`: per-phase report (built, how to run, tests, gaps)
- `CLAUDE.md`: rules for AI-assisted changes

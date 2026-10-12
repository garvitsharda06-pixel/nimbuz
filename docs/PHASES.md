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

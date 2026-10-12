# Decisions log

All dates are IST. "Checked" means the date the fact was verified and how. The sandbox that built
this repo could **not** open broker or exchange websites directly (its egress proxy blocks
`upstox.com`, `nseindia.com`, `nsearchives.nseindia.com`, Yahoo and the others). So facts were
verified two ways:

* **SDK source**: read from the official `upstox-python-sdk` 2.30.0 package on PyPI. This is the
  strongest evidence available here, because it is the vendor's own code.
* **Web search**: search results that quote the official page. Weaker. Marked
  `VERIFY-ON-FIRST-RUN`: the system re-checks these on your machine (see "Runtime verification"
  below), and you should eyeball the linked page once.

---

## D-001 Answers to §14 (owner said "don't ask, don't stop")

The owner told us not to ask, so these are **defaults, not confirmed answers**. Each lives in
`config/settings.yaml` or `.env` and can be changed without touching code.

| # | Question | Default chosen | Where to change |
|---|---|---|---|
| 1 | Broker / data | **Upstox**, read-only **Analytics Token** for market data. Alternatives sit behind the same `MarketDataProvider` interface (Angel One, Fyers, Kite and Dhan are left as stubs). | `.env` `UPSTOX_ANALYTICS_TOKEN` |
| 2 | Capital / risk | Capital **₹5,00,000 (placeholder, set yours)**, risk **0.5 %** per trade, max daily loss **2 %** | `config/settings.yaml` → `risk` |
| 3 | Shorts via | **MIS cash** by default (STT 0.025 % vs 0.05 % on futures). Futures shorts are a config switch; the cost model handles both. | `risk.short_instrument` |
| 4 | 1-min history | Upstox V3 minute candles reportedly start **Jan 2022** (see D-010), which covers the 3-year target. **No paid vendor** without asking. | — |
| 5 | Telegram | **Yes, opt-in.** Disabled until `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set. Sends only to that one chat. | `.env` |
| 6 | Existing code | None (the repo was empty). | — |
| 7 | OS / Docker | Unknown. We support **Docker Compose** (one command) plus native `make` targets on macOS, Linux and Windows-WSL. | — |

## D-002 Never fabricate: data provenance is a first-class field

Every candle file, stats book, backtest report and journal row carries a `provenance` field:
`upstox` (real), `synthetic` (generated), and so on.

* The synthetic generator (`dalal.data.synthetic`) exists **only** so the pipeline can be built
  and tested without network access. It is not market data. All of its output lives under
  `data/synthetic/`, and every UI surface built on it shows a **SYNTHETIC** watermark.
* A stats book built from synthetic data can **never** be loaded in LIVE mode. The engine refuses.
* `board.demo_bypass_gates` lets the replay demo show cards even when nothing qualifies. It works
  **only** when provenance is `synthetic`. Every card it lets through carries
  "GATE BYPASSED: PLUMBING DEMO". The code enforces this, not just the config.
* No backtest numbers in this repo or its docs are presented as real results. Real verdicts come
  from running `make download-data && make backtest` on the owner's machine.

## D-003 Probability, labels, ranking (implements §5)

* Signal: the setup condition holds at the **close of a completed 5-min candle**.
* Entry: a **stop-entry at the trigger price**, valid for `validity_minutes` (default 30). If it
  never triggers, it is not a trade, so it is not an instance.
* Label (triple barrier, on 1-min bars): win = T1 touched before the stop, before 15:15.
  * If one 1-min bar touches both the stop and T1, we count the **stop first** (conservative).
  * A time-out at 15:15 counts as a loss for the probability label, but its actual R still feeds
    expectancy.
* Trade management for expectancy: book 50 % at T1, then move the stop on the rest to breakeven.
  The rest exits at T2, the breakeven stop or 15:15.
* Net R includes the full cost model (D-020) and slippage (D-021).
* p̂ for (setup, side, regime): the hit rate in that bucket, shrunk toward the setup-side mean
  with a Beta prior (strength `k=50`), then run through an **isotonic calibrator** fitted on
  walk-forward OOS predictions.
* CI: Wilson 95 % on the bucket's OOS `(wins, N)`.
* EV(p) = p·W̄ + (1−p)·L̄, where W̄ is the mean net R given a win and L̄ is the mean net R given
  no win (usually negative).
* Conservative EV = EV(Wilson lower bound). Breakeven p_be = −L̄ / (W̄ − L̄).
* Live-board gate (config `board.gate`): N_oos ≥ 200 **and** CI_low > p_be **and**
  conservative EV > 0 **and** walk-forward stability ≥ 60 % of folds with positive OOS
  expectancy (folds with < 20 trades are ignored).
* Grade: A+ if conservative EV ≥ 0.25 R, A if ≥ 0.10 R, else B. B ideas are shown only if they
  pass the gate.

## D-004 Hysteresis (implements §8.3 "no flicker")

An idea on the board stays until it is invalidated, triggers and resolves, expires, or is beaten
by a candidate whose conservative EV is ≥ `board.hysteresis_pct` (20 %) higher **and** which
passes the portfolio constraints with the incumbent removed.

## D-005 Event-driven backtester vs live parity

Feature functions are pure and **causal**: row *t* depends only on rows ≤ *t*. The live engine
recomputes features on the day-so-far frame and takes the last row. The backtester computes on
the full frame and takes every row. Two tests guard this:

1. **Look-ahead test**: perturb or truncate bars after *t*. Features and signals for rows ≤ *t*
   must be bit-identical.
2. **Determinism test**: replaying the same day twice through the live engine gives identical
   signal lists. The replay's signals also equal the backtester's signals for that day.

## D-006 Timestamps

All timestamps are tz-aware `Asia/Kolkata`. A candle `ts` is its **start** (Upstox convention).
A 5-min candle with `ts = 09:15` is available at 09:20:00. Parquet stores UTC instants with the
timezone in metadata.

## D-007 Storage

* Market data: `data/<provenance>/candles_1m/<SYMBOL>.parquet`, one file per instrument, queried
  with DuckDB.
* Derived 5-min and 15-min bars are aggregated on the fly. Aggregating 75 × 5-min bars per day
  per symbol is cheap.
* Journal: SQLite at `data/journal.sqlite`.
* Research outputs: `data/<provenance>/research/` (stats book, OOS trades, tearsheets as JSON).

## D-008 No LLM in the loop

The morning note and EOD report are written with **deterministic templates** filled only with
numbers the system computed. LLM APIs cost money (§3 says ask first). If one is added later, a
validator will reject any output containing a number that is not in the input facts. That
validator is already implemented as `dalal.notify.notes.numbers_subset_check`.

## D-009 Network policy for reference data

Reference fetchers are best-effort and never block. Each has a timeout, a ≥ 2 s delay between
requests to the same host, a fixed polite User-Agent, a disk cache, and a `stale` flag that the
UI shows. Sources we have **no permitted, reliable free source** for are shown as "—":

* US index futures
* GIFT Nifty
* FII/DII provisional flows: NSE serves these only through its website's cookie-gated JSON API,
  which its terms don't clearly permit automated use of. Default **off**. The owner can enable it
  and accept that themselves.

USD/INR and crude can come from Upstox (NSE currency futures, MCX crude) through the same feed
when instrument keys resolve.

---

## Verified facts

### D-010 Upstox: hosts, endpoints, token (checked 2026-10-12)

| Fact | Value | Source |
|---|---|---|
| REST host | `https://api.upstox.com` (orders use `https://api-hft.upstox.com`) | SDK source `upstox_client/configuration.py` (upstox-python-sdk 2.30.0) |
| Historical candles V3 | `GET /v3/historical-candle/{instrument_key}/{unit}/{interval}/{to_date}/{from_date}` (`unit` ∈ minutes/hours/days/weeks/months) | SDK `api/history_v3_api.py` |
| Intraday candles V3 (today) | `GET /v3/historical-candle/intraday/{instrument_key}/{unit}/{interval}` | SDK `api/history_v3_api.py` |
| Quotes V3 | `GET /v3/market-quote/quotes`, `/ltp`, `/ohlc` | SDK `api/market_quote_v3_api.py` |
| Holidays / timings | `GET /v2/market/holidays`, `/v2/market/holidays/{date}`, `/v2/market/timings/{date}`, `/v2/market/status/{exchange}` | SDK `api/market_holidays_and_timings_api.py` |
| WebSocket V3 | `wss://api.upstox.com/v3/feed/market-data-feed`, header `Authorization: Bearer <token>`; requests are **binary frames** containing JSON `{guid, method: sub/unsub/change_mode, data:{instrumentKeys, mode}}`; responses are protobuf `FeedResponse` (package `com.upstox.marketdatafeederv3udapi.rpc.proto`) | SDK `feeder/market_data_feeder_v3.py`, `feeder/proto/MarketDataFeedV3_pb2.py` (decoded) |
| Feed modes | `ltpc`, `full` (5-level depth, OHLC, ATP, VTT, OI, IV, TBQ/TSQ), `option_greeks`, `full_d30` | SDK + [Upstox Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed) (via search) |
| Feed limits | 2 connections per user. LTPC: 5,000 keys (individual) / 2,000 (combined). Full: 2,000 / 1,500. `VERIFY-ON-FIRST-RUN` | [Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed) (via search) |
| REST rate limits | 25 req/s, 250 req/min, 1,000 req/30 min, per API per user. Breaching them can suspend access temporarily. The downloader enforces all three windows. | [Upstox Rate Limits](https://upstox.com/developer/api-documentation/rate-limiting) (via search) |
| Analytics Token | Long-lived, no daily login. Covers **Market Data** and **Realtime & Streaming** APIs. Cannot place orders. `VERIFY-ON-FIRST-RUN`: whether historical candles are included. The adapter probes this at start-up and records the result. | [Upstox API overview](https://upstox.com/developer/api-documentation/api-overview) (via search) |
| Minute history depth | Minute/hour candles **since Jan 2022**; day/week/month since Jan 2000. For minute data, `to_date` must fall within **one month** of `from_date`, so the downloader fetches in 1-month chunks. | [Upstox community release note](https://community.upstox.com/t/get-historical-data-in-any-time-frame-you-wished-we-built-new-releases-at-upstox-api/8845) (staff post, via search). `VERIFY-ON-FIRST-RUN` |
| Instrument master | `https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz` (also per exchange, e.g. `NSE.json.gz`). Public CDN, refreshed around 06:00 IST. | [community thread](https://community.upstox.com/t/complete-is-full-file-of-nse-fo-nse-eq-bse-fo-bse-eq-mcx-etc/10294) (via search) |
| "Official agent skill packs" | **Not found** in search (2026-10-12). Nothing installed. Re-check at upstox.com/developer. | search |

Security note: the official SDK feeder sets `ssl.CERT_NONE` (TLS verification **disabled**).
We do **not** use the SDK's feeder for that reason. Our client (`dalal.data.upstox`) uses the
`websockets` library with normal certificate verification and decodes the same protobuf schema.

### D-020 Indian cost model (checked 2026-10-12)

| Item | Cash intraday (MIS) | Stock futures | Source |
|---|---|---|---|
| Brokerage (Upstox) | min(₹20, 0.1 %) per executed order | min(₹20, 0.05 %) per executed order | [upstox.com/brokerage-charges](https://upstox.com/brokerage-charges/) (via search) — older pages showed 0.05 % intraday; `VERIFY-ON-FIRST-RUN` |
| STT | 0.025 % sell side | **0.05 % sell side (from 1 Apr 2026, Budget 2026; was 0.02 %)** | [Zerodha STT](https://support.zerodha.com/category/account-opening/resident-individual/ri-charges/articles/how-is-the-securities-transaction-tax-stt-calculated), [ICICI Direct](https://www.icicidirect.com/ilearn/futures-and-options/articles/stt-changes-in-budget-2026-what-f-o-traders-should-know) |
| NSE transaction charge | 0.00297 % of turnover | 0.00173 % of turnover | Zerodha charges page (via search). NSE circular [FA73061](https://nsearchives.nseindia.com/content/circulars/FA73061.pdf) (27 Feb 2026) rolled back the IPFT contribution "with corresponding adjustment" to transaction charges, with **overall member outflow unchanged**. Some April-2026 pages show 0.00307 % / 0.00183 % (that is, including ₹10/cr IPFT). We use the **higher** pair, 0.00307 % / 0.00183 %, which includes IPFT, to stay conservative. `VERIFY-ON-FIRST-RUN` |
| SEBI turnover fee | ₹10 per crore (0.0001 %) | ₹10 per crore | Rupeezy / Zerodha (via search) |
| Stamp duty | 0.003 % buy side | 0.002 % buy side | Upstox charge list (via search) |
| GST | 18 % on (brokerage + exchange txn + SEBI fee) | same | Zerodha (via search) |

All rates live in `config/costs.yaml`, each with its source and check date. **The cost model is
applied to both legs of every simulated trade.**

### D-021 Slippage model (our assumption, not a fact)

Fill = trigger ± max(1 tick, `spread_bps`/2 + `k_vol`·σ_1m·price), with `spread_bps` = 3 for
F&O-universe names and `k_vol` = 0.10. This is deliberately pessimistic for liquid F&O names.
Sensitivity to slippage is reported in every tearsheet (0.5×, 1×, 2×).

### D-030 SEBI retail algo framework (checked 2026-10-12, relevant only to Phase 7)

In force from 1 Apr 2026:

* API orders only from a whitelisted static IP (changeable at most once a week)
* OAuth plus mandatory 2FA, with sessions ending daily
* At or above 10 orders/sec per exchange, the strategy must be registered
* Black-box algo providers must be SEBI Research Analysts

Sources: [Fyers notice](https://fyers.in/notice-board/new-sebi-framework-for-retail-algo-trading-from-april-01-2026/), [Zerodha Daily Brief](https://thedailybrief.zerodha.com/p/sebis-latest-algo-trading-rules), [Angel One](https://www.angelone.in/news/sebi-new-rules-for-retail-algo-trading-key-changes-retail-traders-can-expect) (via search).

**Phase 7 is not built.** This terminal places no orders.

Sharing buy/sell calls with others requires SEBI Research Analyst registration, so the Telegram
notifier sends **only** to the configured chat ID. There is no broadcast or group feature.

### D-031 NSE holidays 2026 (checked 2026-10-12)

Weekday equity holidays: 26 Jan, 3 Mar, 26 Mar, 31 Mar, 3 Apr, 14 Apr, 1 May, 28 May, 26 Jun,
14 Sep, 2 Oct, 20 Oct, 10 Nov, 24 Nov, 25 Dec. Some sources also list **15 Jan** (Maharashtra
municipal election); we include it. Muhurat trading: **Sun 8 Nov 2026**, timings by circular
(not yet known; the config entry is marked TBA).

Sources: [Bajaj Broking](https://www.bajajbroking.in/nse-holidays), [ClearTax](https://cleartax.in/s/nse-holidays-2026) (via search).

**Primary source at runtime**: the Upstox `/v2/market/holidays` API. The config file is only the
fallback.

### D-032 TradingView Lightweight Charts licence

Apache-2.0. We must credit TradingView and link to tradingview.com where users can see it. We
keep `attributionLogo: true` on every chart and add a credit line in the About panel.

Source: [lightweight-charts README 5.0.7](https://unpkg.com/lightweight-charts@5.0.7/README.md) (via search).

---

## Runtime verification (`make verify`)

On the owner's machine, `dalal verify` does three things:

1. Hits the instrument master, the holidays API and one historical-candle call with the
   Analytics Token. It records which API categories the token can reach, the earliest minute
   date available, and the observed rate-limit headers, then writes the results to
   `data/verify_report.json`.
2. Prints the `VERIFY-ON-FIRST-RUN` items above, with their URLs, so the owner can tick them off.
3. Leaves `config/costs.yaml` rates unchanged. They are never auto-edited; changing them is a
   human decision recorded here.

## Known data gaps (honest list)

* **Survivorship bias**: the universe is built from today's instrument master, so stocks that
  left F&O since 2022 are missing from history. From the first run on, we store a daily universe
  snapshot (`data/upstox/universe/YYYY-MM-DD.json`). Historical results are biased toward
  survivors. The tearsheet footnotes say so.
* **Corporate actions**: whether Upstox candles are split/bonus-adjusted is unverified.
  * The downloader detects overnight gaps of more than 30 % on stocks.
  * It applies factors from `config/reference/corporate_actions.csv` (owner-maintained,
    best-effort from NSE corporate-action files).
  * Unexplained gaps are flagged. The two days around them are excluded from prev-day/ATR
    features.
* **Futures OI history**: Upstox expired-instrument candles give OI only for expired contracts,
  and only where the API provides it. The OI confirmation setup stays **disabled** until it has
  N ≥ 200 OOS instances.

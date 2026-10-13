"""`dalal` command-line interface."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import typer

from dalal.config import get_secrets, get_settings
from dalal.log import setup_logging

app = typer.Typer(add_completion=False, help="Dalal Desk: NSE intraday research terminal (no order placement).")


@app.callback()
def _main(verbose: bool = typer.Option(False, "-v")) -> None:
    setup_logging("DEBUG" if verbose else "INFO")


@app.command()
def synth(symbols: int = typer.Option(None), days: int = typer.Option(None), seed: int = typer.Option(None),
          momentum: float = typer.Option(None, help="planted idiosyncratic momentum (known-answer tests)")) -> None:
    """Generate the SYNTHETIC dataset (plumbing/demo only, never market data)."""
    from dalal.data.synthetic import SynthParams, generate

    s = get_settings()
    sc = s.synthetic
    p = SynthParams(symbols=symbols or sc.symbols, days=days or sc.days, seed=seed if seed is not None else sc.seed,
                    start=sc.start, idio_momentum=sc.idio_momentum if momentum is None else momentum,
                    trend_day_drift_bps=sc.trend_day_drift_bps)
    store = generate(s.data.root_path, p)
    m = store.manifest()
    typer.echo(f"SYNTHETIC dataset written to {store.base}: {len(store.keys())} instruments, "
               f"{m['n_days']} days {m['days'][0]} → {m['days'][1]}")


@app.command("check-data")
def check_data(provenance: str = typer.Option(None)) -> None:
    """Offline data-quality checks on the stored candles."""
    from dalal.data.quality import check_candles
    from dalal.data.store import CandleStore

    s = get_settings()
    store = CandleStore(s.data.root_path, provenance or s.data.provider)
    insts, _ = store.instruments()
    bad = 0
    for inst in insts:
        if not store.has(inst.key):
            continue
        rep = check_candles(inst.key, store.read(inst.key), is_index=inst.kind == "INDEX")
        flag = "OK " if rep.ok else "BAD"
        bad += 0 if rep.ok else 1
        issues = {k: v for k, v in rep.issues.items() if v}
        typer.echo(f"{flag} {inst.symbol:<14} rows={rep.rows:>8} days={rep.days:>4} {issues or ''}"
                   f"{' gaps>30%: ' + str(rep.suspicious_gaps[:3]) if rep.suspicious_gaps else ''}")
    typer.echo(f"\n{'ALL CHECKS PASSED' if bad == 0 else f'{bad} instrument(s) FAILED hard checks'}")
    raise typer.Exit(1 if bad else 0)


@app.command()
def universe(asof: str = typer.Option(None, help="YYYY-MM-DD (default: latest stored day)")) -> None:
    """Build and print the trading universe for a day."""
    from dalal.data.store import CandleStore
    from dalal.data.universe import build_universe

    s = get_settings()
    store = CandleStore(s.data.root_path, s.data.provider)
    insts, roles = store.instruments()
    days = store.days(roles["nifty"])
    d = date.fromisoformat(asof) if asof else days[-1]
    u = build_universe(store, insts, d, s.universe, roles["nifty"], short_via_futures=s.risk.short_instrument == "futures")
    typer.echo(f"Universe {d}: {len(u.members)} members, {len(u.excluded)} excluded")
    for m in u.members:
        typer.echo(f"  {m.symbol:<10} {m.sector:<8} medTV ₹{m.median_tv_cr:>8.1f}cr  last {m.last_close:>9.2f}  β {m.beta}")
    for sym, why in u.excluded.items():
        typer.echo(f"  ✗ {sym:<10} {why}")


@app.command()
def verify() -> None:
    """Probe Upstox with your token: which API categories work, earliest minute data, sample fields."""
    from dalal.data.upstox import UpstoxREST

    s = get_settings()
    tok = get_secrets().upstox_analytics_token
    report: dict[str, object] = {"checked": date.today().isoformat()}

    async def run() -> None:
        rest = UpstoxREST(s.data.upstox, tok.get_secret_value() if tok else None)
        try:
            rows = await rest.instrument_rows()
            report["instruments"] = {"ok": True, "rows": len(rows), "sample": rows[:2]}
        except Exception as e:
            report["instruments"] = {"ok": False, "error": repr(e)}
        if tok is None:
            report["token"] = "UPSTOX_ANALYTICS_TOKEN not set"
            return
        for name, coro in [("holidays", rest.holidays()),
                           ("historical_1m_2022", rest.historical_1m(s.data.upstox.index_keys.get("nifty", "NSE_INDEX|Nifty 50"),
                                                                     date(2022, 1, 3), date(2022, 1, 7))),
                           ("intraday_1m", rest.intraday_1m(s.data.upstox.index_keys.get("nifty", "NSE_INDEX|Nifty 50")))]:
            try:
                r = await coro
                report[name] = {"ok": True, "n": len(r)}
            except Exception as e:
                report[name] = {"ok": False, "error": repr(e)[:300]}
        await rest.close()

    asyncio.run(run())
    out = s.data.root_path / "verify_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    typer.echo(json.dumps(report, indent=2, default=str)[:4000])
    typer.echo("\nAlso tick off the VERIFY-ON-FIRST-RUN items in docs/DECISIONS.md (charges, limits, token scope).")


@app.command("download-data")
def download_data(years: int = typer.Option(None), limit: int = typer.Option(0, help="only first N instruments")) -> None:
    """Download ≥3y of 1-minute candles for the F&O universe + indices from Upstox (rate-limited)."""
    from dalal.data.download import download_history
    from dalal.data.reference import ReferenceData
    from dalal.data.store import CandleStore
    from dalal.data.upstox import UpstoxProvider

    s = get_settings()
    tok = get_secrets().upstox_analytics_token
    if tok is None:
        typer.echo("UPSTOX_ANALYTICS_TOKEN missing in .env")
        raise typer.Exit(2)
    ref = ReferenceData(s.data.root_path / "reference")
    sectors = ref.sector_map()
    prov = UpstoxProvider(s.data.upstox, tok.get_secret_value(), sectors.data or {})
    store = CandleStore(s.data.root_path, "upstox")

    async def run() -> None:
        insts = await prov.instruments()
        roles = prov.index_roles()
        idx_keys = set(roles.all_keys())
        eq = [i for i in insts if i.kind == "EQ" and i.is_fno]
        idx = [i for i in insts if i.kind == "INDEX" and i.key in idx_keys]
        targets = idx + eq
        if limit:
            targets = targets[:limit]
        store.write_instruments([i for i in insts if i.kind in ("EQ", "INDEX") and (i.is_fno or i.key in idx_keys)] +
                                [i for i in insts if i.kind == "FUT"],
                                {"nifty": roles.nifty, "banknifty": roles.banknifty, "vix": roles.vix, "sectors": roles.sectors})
        typer.echo(f"{len(eq)} F&O equities + {len(idx)} indices → downloading (sector map stale={sectors.stale})")
        counts = await download_history(prov, store, targets, years or s.data.history_years,
                                        progress=lambda i, n, sym, rows: typer.echo(f"[{i}/{n}] {sym}: {rows} rows"))
        store.write_manifest({"kind": "upstox historical 1m", "counts": counts, "sector_source": sectors.source,
                              "sector_stale": sectors.stale})
        await prov.rest.close()

    asyncio.run(run())


@app.command()
def serve(replay: bool = typer.Option(False, help="start a replay immediately"),
          day: str = typer.Option(None), speed: float = typer.Option(None),
          live: bool = typer.Option(False),
          demo: bool = typer.Option(False, help="bypass the board gate to see the plumbing (honoured ONLY on synthetic data)"),
          start_time: str = typer.Option(None, help="replay: fast-forward to HH:MM"),
          port: int = typer.Option(None, help="override api.port (host stays 127.0.0.1)")) -> None:
    """Run the API/WebSocket server (127.0.0.1 by default)."""
    import uvicorn

    from dalal.api.app import create_app
    from dalal.config import load_settings

    s = load_settings(overrides={"board": {"demo_bypass_gates": True}}) if demo else get_settings()
    if demo and s.data.provider != "synthetic":
        typer.secho("--demo is ignored: the gate bypass only works on synthetic data (DECISIONS D-002).", fg="yellow")
    api = create_app(s)
    if replay or live:
        @api.on_event("startup")
        async def _start() -> None:
            rt = api.state.rt
            if live:
                await rt.start_live()
            else:
                await rt.start_replay(date.fromisoformat(day) if day else None, speed, start_time)

    import os

    host = os.environ.get("DALAL_API_HOST", s.api.host)      # docker-compose sets 0.0.0.0 inside the container
    uvicorn.run(api, host=host, port=port or s.api.port, log_level="warning")


def _register_research_commands() -> None:
    try:
        from dalal.backtest import cli as bcli
        bcli.register(app)
    except ImportError:
        pass


_register_research_commands()


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()

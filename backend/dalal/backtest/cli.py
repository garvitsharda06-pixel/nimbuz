"""`dalal backtest`: research CLI."""

from __future__ import annotations

import typer

from dalal.config import get_settings


def register(app: typer.Typer) -> None:
    @app.command()
    def backtest(sensitivity: bool = typer.Option(False, help="also run the pre-declared parameter grids"),
                 symbols: str = typer.Option(None, help="comma-separated subset"),
                 setups: str = typer.Option(None, help="comma-separated subset of setups"),
                 provenance: str = typer.Option(None),
                 start: str = typer.Option(None, help="YYYY-MM-DD first day of signals"),
                 end: str = typer.Option(None, help="YYYY-MM-DD last day (e.g. the day before a replay day)")) -> None:
        """Walk-forward research: stats book, calibration and tearsheets, with an honest verdict per setup."""
        from dalal.backtest.run import run_research
        from dalal.config import load_settings
        from dalal.data.store import CandleStore

        s = get_settings()
        if start or end:
            s = load_settings(overrides={"backtest": {"start": start, "end": end}})
        prov = provenance or s.data.provider
        store = CandleStore(s.data.root_path, prov)
        if prov == "synthetic":
            typer.secho("SYNTHETIC DATA: these results test the plumbing only and say NOTHING about real markets.",
                        fg="yellow")
        summ = run_research(s, store, sensitivity, symbols.split(",") if symbols else None,
                            setups.split(",") if setups else None, progress=lambda m: typer.echo(m, err=True))
        if "error" in summ:
            typer.echo(summ["error"])
            raise typer.Exit(1)
        cal = summ["calibration"]
        typer.echo(f"\nData {summ['data_range'][0]} → {summ['data_range'][1]} | {summ['n_symbols']} symbols | "
                   f"{summ['n_instances']} instances, {summ['n_oos']} out-of-sample | {summ['elapsed_s']}s")
        if cal.get("n"):
            typer.echo(f"Calibration (OOS, forward-chained): Brier base {cal['brier_base']:.4f} · raw "
                       f"{cal['brier_raw']:.4f} · isotonic {cal['brier_cal']:.4f}  (lower is better)")
            typer.echo("Reliability (held-out, isotonic forward-chained):  pred → observed [95% CI] (n)")
            for r in cal.get("reliability_cal", []):
                typer.echo(f"   {r['pred']:.2f} → {r['obs']:.2f} [{r['obs_lo']:.2f}, {r['obs_hi']:.2f}] ({r['n']})")
        typer.echo(f"\n{'SETUP':<16}{'SIDE':<7}{'VERDICT':<19}{'N_OOS':>6}{'HIT':>7}{'EXP R':>8}{'consEV':>8}"
                   f"{'STAB':>6}{'DSR':>6}")
        for v in summ["verdicts"]:
            f = lambda x, fmt: (fmt.format(x) if x is not None else "—")  # noqa: E731
            typer.echo(f"{v['setup']:<16}{v['side']:<7}{v['verdict']:<19}{v['n_oos']:>6}{f(v['hit'], '{:.1%}'):>7}"
                       f"{f(v['exp_r'], '{:+.3f}'):>8}{f(v['cons_ev'], '{:+.3f}'):>8}{f(v['stability'], '{:.0%}'):>6}"
                       f"{f(v['dsr'], '{:.2f}'):>6}")
        for v in summ["verdicts"]:
            typer.echo(f"  · {v['setup']}/{v['side']}: {v['why']}")
        mm = summ.get("meta_model") or {}
        typer.echo(f"\nMeta-model (LightGBM, purged walk-forward): {mm.get('reason', '—')}")
        typer.echo(f"\nArtifacts: {store.base / 'research'}")

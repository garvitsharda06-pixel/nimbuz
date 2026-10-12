import clsx from "clsx";
import { useMemo } from "react";
import { axisStyle, EChart } from "../components/EChart";
import { dash, isNum, pctRaw, px } from "../lib/format";
import { useTerminal } from "../store/terminal";
import { Rotation } from "./Rotation";

export function Market() {
  const m = useTerminal((s) => s.market);
  const sectors = m?.sectors ?? [];
  const treemap = useMemo(() => ({
    tooltip: { formatter: (p: { name: string; value: number[] }) => `${p.name}: ${pctRaw(p.value?.[1])}` },
    series: [{
      type: "treemap", roam: false, nodeClick: false, breadcrumb: { show: false }, width: "100%", height: "100%",
      label: { show: true, formatter: (p: { name: string; value: number[] }) => `${p.name}\n${pctRaw(p.value?.[1])}`, fontFamily: "Inter", fontSize: 12 },
      itemStyle: { borderColor: "#07080a", borderWidth: 2, gapWidth: 2 },
      data: sectors.map((s) => {
        const v = s.ret_pct ?? 0;
        const a = Math.min(1, Math.abs(v) / 1.5);
        return { name: s.name, value: [1, v], itemStyle: { color: v >= 0 ? `rgba(34,197,94,${0.15 + 0.6 * a})` : `rgba(239,68,68,${0.15 + 0.6 * a})` } };
      }),
    }],
  }), [sectors]);
  const breadth = useMemo(() => ({
    grid: { left: 36, right: 10, top: 10, bottom: 24 },
    xAxis: { type: "category", data: (m?.breadth_series ?? []).map((_, i) => i), ...axisStyle, axisLabel: { show: false } },
    yAxis: { type: "value", min: 0, max: 100, ...axisStyle },
    series: [{ type: "line", data: m?.breadth_series ?? [], smooth: true, showSymbol: false, lineStyle: { color: "#60a5fa", width: 1.5 },
      markLine: { silent: true, symbol: "none", lineStyle: { color: "#3f4654" }, data: [{ yAxis: 50 }] } }],
    tooltip: { trigger: "axis" },
  }), [m?.breadth_series]);
  if (!m || !m.as_of) return <div className="p-4 text-mute">Market data appears after the first completed 5-minute candle of a session.</div>;
  return (
    <div className="p-3 grid grid-cols-1 xl:grid-cols-3 gap-3">
      <Panel title={`Sector heatmap (return vs prev close) · ${m.as_of}`} className="xl:col-span-2">
        <EChart option={treemap as never} height={300} testid="treemap" />
      </Panel>
      <Panel title="Breadth: % of universe above VWAP (today)">
        <EChart option={breadth as never} height={300} />
        <div className="text-[11.5px] text-mute">Regime <span className="text-ink">{m.regime?.replace(/_/g, " ")}</span> · score <span className="num text-ink">{m.regime_score ?? dash}</span> · advancers <span className="num text-ink">{m.adv_pct ?? dash}%</span> · universe <span className="num text-ink">{m.universe ?? dash}</span> (excluded {m.excluded ?? dash})</div>
      </Panel>
      <Panel title="Sector rotation vs Nifty (relative strength × momentum)" className="xl:col-span-2">
        <Rotation />
      </Panel>
      <Panel title="Sector ranking">
        <table className="w-full text-[12px]">
          <thead><tr className="text-mute text-left"><th className="font-normal">#</th><th className="font-normal">Sector</th><th className="font-normal text-right">vs prev close</th><th className="font-normal text-right">vs open</th></tr></thead>
          <tbody>{sectors.map((s) => (
            <tr key={s.name} className="border-t hair"><td className="num">{s.rank ?? dash}</td><td>{s.name}</td>
              <td className={clsx("num text-right", (s.ret_pct ?? 0) >= 0 ? "text-long" : "text-short")}>{pctRaw(s.ret_pct)}</td>
              <td className="num text-right text-mute">{pctRaw(s.ret_open_pct)}</td></tr>
          ))}</tbody>
        </table>
      </Panel>
      <Panel title="Volume shockers (cumulative RVOL)">
        <MoverTable rows={m.volume_shockers ?? []} />
      </Panel>
      <Panel title="RS leaders vs Nifty (σ)">
        <MoverTable rows={m.rs_leaders ?? []} />
      </Panel>
      <Panel title="RS laggards vs Nifty (σ)">
        <MoverTable rows={m.rs_laggards ?? []} />
      </Panel>
      <Panel title="F&O OI build-up" className="xl:col-span-2">
        <OIPanel />
      </Panel>
      <Panel title="Today's events / announcements">
        <Events />
      </Panel>
    </div>
  );
}

export function Panel({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={clsx("rounded-md border hair bg-panel p-2.5", className)}>
      <div className="label pb-2">{title}</div>
      {children}
    </section>
  );
}

function MoverTable({ rows }: { rows: { symbol: string; sector: string; rvol: number; chg_pct: number; ltp: number; rs_z: number | null }[] }) {
  if (!rows.length) return <div className="text-mute text-[12px]">insufficient data</div>;
  return (
    <table className="w-full text-[12px]">
      <thead><tr className="text-mute text-left"><th className="font-normal">Symbol</th><th className="font-normal text-right">LTP</th><th className="font-normal text-right">Chg</th><th className="font-normal text-right">RVOL</th><th className="font-normal text-right">RS σ</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.symbol} className="border-t hair">
          <td>{r.symbol} <span className="text-mute text-[10.5px]">{r.sector}</span></td>
          <td className="num text-right">{px(r.ltp)}</td>
          <td className={clsx("num text-right", r.chg_pct >= 0 ? "text-long" : "text-short")}>{pctRaw(r.chg_pct)}</td>
          <td className="num text-right">{r.rvol.toFixed(1)}×</td>
          <td className="num text-right">{isNum(r.rs_z) ? r.rs_z!.toFixed(1) : dash}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function OIPanel() {
  return (
    <div className="text-[12px] text-mute">
      Futures open-interest analytics (long build-up / short build-up / short covering / long unwinding) need real-time futures
      OI from the broker feed (Upstox <span className="num">full</span> mode carries OI). This data source provides none, so the
      panel shows nothing rather than invented numbers.
    </div>
  );
}

function Events() {
  return (
    <div className="text-[12px] text-mute">
      Events come from <span className="num">config/reference/events.csv</span> and <span className="num">config/calendar.yaml</span>
      (blackouts). Names with results or board meetings today are excluded from the universe by default.
    </div>
  );
}

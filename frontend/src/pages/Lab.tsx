import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { axisStyle, EChart } from "../components/EChart";
import { getJSON, postJSON } from "../lib/api";
import { dash, int, isNum, pct, r, rupees } from "../lib/format";
import { Panel } from "./Market";

interface Verdict { setup: string; label: string; side: string; verdict: string; why: string; n_oos: number; hit: number | null; exp_r: number | null; cons_ev: number | null; stability: number | null; dsr: number | null; qualified_regimes: string[] }
interface Summary {
  available: boolean; provenance: string; synthetic: boolean; created: string; data_range: [string, string]; n_symbols: number;
  n_instances: number; n_oos: number; elapsed_s: number; verdicts: Verdict[]; trials: Record<string, number>;
  calibration: { n: number; brier_base: number; brier_raw: number; brier_cal: number; reliability_cal: { pred: number; obs: number; obs_lo: number; obs_hi: number; n: number }[] };
  meta_model?: { shipped: boolean; reason: string; brier_rule?: number; brier_meta?: number; auc_rule?: number; auc_meta?: number; sel_exp_rule?: number; sel_exp_meta?: number };
  folds: { k: number; test_start: string; test_end: string }[];
}
type Stats = Record<string, number | null>;
interface Tearsheet {
  setup: string; label: string; n_oos: number; n_is: number; provenance: string;
  sides: Record<string, { verdict: string; why: string; oos: Stats; is: Stats; full: Stats; dsr: Stats; mc: Stats; regimes: { regime: string; n: number; hit: number; expectancy: number; cons_ev: number; qualified: boolean; reasons: string[] }[] }>;
  equity_oos: { day: string; cum_r: number; cum_inr: number; dd_r: number }[];
  folds: { fold: number; start: string; end: string; n: number; hit: number; exp_r: number; pred: number }[];
  calibration: { pred: number; obs: number; obs_lo: number; obs_hi: number; n: number }[];
  breakdowns: Record<string, ({ bucket: string } & Stats)[]>;
  mae_hist_losers: { lo: number; hi: number; n: number }[]; mfe_hist_winners: { lo: number; hi: number; n: number }[];
  avg_win_r: number | null; avg_loss_r: number | null; avg_win_inr: number | null; avg_loss_inr: number | null; avg_cost_inr: number | null;
  trades_per_day: number; max_dd_r: number | null; exit_reasons: Record<string, number>;
  sensitivity: { variant: string; params: Record<string, string>; n: number; exp_r: number; hit: number }[];
}

const vTone = (v: string) => (v === "EDGE" ? "text-long border-long/50" : v === "MARGINAL" ? "text-warn border-warn/50" : v === "NO EDGE" ? "text-short border-short/50" : "text-mute border-line");

export function Lab() {
  const sq = useQuery({ queryKey: ["summary"], queryFn: () => getJSON<Summary>("/api/research/summary") });
  const st = useQuery({ queryKey: ["setups"], queryFn: () => getJSON<{ setups: { name: string; label: string; enabled: boolean }[] }>("/api/setups") });
  const [sel, setSel] = useState<string | null>(null);
  const s = sq.data;
  if (!s) return <div className="p-4 text-mute">Loading…</div>;
  if (!s.available) return <div className="p-4 text-mute">No research yet. Run <span className="num">make backtest</span>.</div>;
  const setups = Array.from(new Set(s.verdicts.map((v) => v.setup)));
  const cur = sel ?? setups[0];
  return (
    <div className="p-3 space-y-3">
      {s.synthetic && <div className="rounded border border-warn/40 bg-warn/10 text-warn px-3 py-1.5 text-[12px]">These results come from SYNTHETIC data. They test the plumbing and say nothing about real NSE stocks. Run <span className="num">make download-data && make backtest</span> for real verdicts.</div>}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Panel title={`Verdicts (walk-forward OOS, net of costs) · ${s.data_range[0]} → ${s.data_range[1]} · ${s.n_symbols} symbols · ${int(s.n_oos)} OOS instances`} className="xl:col-span-2">
          <div className="overflow-x-auto scroll-thin">
            <table className="w-full text-[12px]" data-testid="verdicts">
              <thead><tr className="text-mute text-left"><th className="font-normal">Setup</th><th className="font-normal">Side</th><th className="font-normal">Verdict</th><th className="font-normal text-right">N OOS</th><th className="font-normal text-right">Hit</th><th className="font-normal text-right">Exp R</th><th className="font-normal text-right">Cons EV</th><th className="font-normal text-right">WF stab.</th><th className="font-normal text-right">DSR</th><th className="font-normal text-right">Trials</th><th className="font-normal">Live</th></tr></thead>
              <tbody>{s.verdicts.map((v) => {
                const en = st.data?.setups.find((x) => x.name === v.setup)?.enabled;
                return (
                  <tr key={v.setup + v.side} className={clsx("border-t hair cursor-pointer hover:bg-panel2", cur === v.setup && "bg-panel2")} onClick={() => setSel(v.setup)} title={v.why}>
                    <td>{v.label}</td><td className={v.side === "long" ? "text-long" : "text-short"}>{v.side}</td>
                    <td><span className={clsx("px-1.5 py-0.5 rounded border text-[10.5px]", vTone(v.verdict))}>{v.verdict}</span></td>
                    <td className="num text-right">{int(v.n_oos)}</td><td className="num text-right">{pct(v.hit, 1)}</td>
                    <td className={clsx("num text-right", (v.exp_r ?? 0) > 0 ? "text-long" : "text-short")}>{r(v.exp_r, 3)}</td>
                    <td className="num text-right">{r(v.cons_ev, 3)}</td><td className="num text-right">{pct(v.stability)}</td>
                    <td className="num text-right">{isNum(v.dsr) ? v.dsr!.toFixed(2) : dash}</td><td className="num text-right">{s.trials[v.setup] ?? dash}</td>
                    <td>{v.side === "long" && en !== undefined && (
                      <button className={clsx("px-1.5 py-0.5 rounded border text-[10.5px]", en ? "border-long/50 text-long" : "hair text-mute")}
                        onClick={async (e) => { e.stopPropagation(); await postJSON(`/api/setups/${v.setup}?enabled=${!en}`); st.refetch(); }}>
                        {en ? "enabled" : "disabled"}
                      </button>)}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="text-[11px] text-mute pt-2">Enabled ≠ shown: a setup reaches the board only if it passes the gate (N ≥ 200 OOS, CI low &gt; breakeven, conservative EV &gt; 0, WF stability). Trials = variants ever tried, used in the deflated-Sharpe haircut.</div>
        </Panel>
        <Panel title="Calibration (held-out, forward-chained) & meta-model">
          <div className="grid grid-cols-3 gap-2 text-[12px] pb-2">
            <div><div className="label">Brier base</div><div className="num">{s.calibration.brier_base?.toFixed(4) ?? dash}</div></div>
            <div><div className="label">Rule book</div><div className="num">{s.calibration.brier_raw?.toFixed(4) ?? dash}</div></div>
            <div><div className="label">+ isotonic</div><div className="num">{s.calibration.brier_cal?.toFixed(4) ?? dash}</div></div>
          </div>
          <Reliability rows={s.calibration.reliability_cal ?? []} />
          <div className={clsx("text-[12px] pt-2", s.meta_model?.shipped ? "text-long" : "text-mute")} data-testid="meta">
            LightGBM meta-model: {s.meta_model?.reason ?? dash}
            {isNum(s.meta_model?.sel_exp_meta) && <span className="block text-mute">Top-30 % selection expectancy: meta {r(s.meta_model!.sel_exp_meta)} vs rule {r(s.meta_model!.sel_exp_rule)}</span>}
          </div>
        </Panel>
      </div>
      {cur && <TearsheetView setup={cur} />}
    </div>
  );
}

function Reliability({ rows, height = 220 }: { rows: { pred: number; obs: number; obs_lo: number; obs_hi: number; n: number }[]; height?: number }) {
  const opt = useMemo(() => ({
    grid: { left: 36, right: 10, top: 10, bottom: 26 },
    xAxis: { type: "value", min: 0, max: 1, ...axisStyle }, yAxis: { type: "value", min: 0, max: 1, ...axisStyle },
    series: [
      { type: "line", data: [[0, 0], [1, 1]], showSymbol: false, lineStyle: { color: "#3f4654", type: "dashed" } },
      { type: "scatter", data: rows.map((b) => [b.pred, b.obs, b.n]), symbolSize: (v: number[]) => Math.max(5, Math.min(22, Math.sqrt(v[2]) / 3)), itemStyle: { color: "#60a5fa" } },
      { type: "custom", renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
          const lo = api.coord([api.value(0), api.value(1)]), hi = api.coord([api.value(0), api.value(2)]);
          return { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: { stroke: "#60a5fa" } };
        }, data: rows.map((b) => [b.pred, b.obs_lo, b.obs_hi]) },
    ],
  }), [rows]);
  return rows.length ? <EChart option={opt as never} height={height} /> : <div className="text-mute text-[12px]">insufficient data</div>;
}

function TearsheetView({ setup }: { setup: string }) {
  const q = useQuery({ queryKey: ["ts", setup], queryFn: () => getJSON<Tearsheet>(`/api/research/tearsheet/${setup}`) });
  const t = q.data;
  const eq = useMemo(() => t && ({
    grid: { left: 50, right: 50, top: 14, bottom: 28 },
    xAxis: { type: "category", data: t.equity_oos.map((e) => e.day), ...axisStyle },
    yAxis: [{ type: "value", name: "R", ...axisStyle }, { type: "value", name: "₹", ...axisStyle, splitLine: { show: false } }],
    tooltip: { trigger: "axis" }, legend: { textStyle: { color: "#7c8694" }, top: 0 },
    series: [
      { name: "cum R", type: "line", data: t.equity_oos.map((e) => e.cum_r), showSymbol: false, lineStyle: { color: "#60a5fa" } },
      { name: "cum ₹", type: "line", yAxisIndex: 1, data: t.equity_oos.map((e) => e.cum_inr), showSymbol: false, lineStyle: { color: "#a1a1aa", width: 1 } },
      { name: "drawdown R", type: "line", data: t.equity_oos.map((e) => -e.dd_r), showSymbol: false, lineStyle: { color: "#ef4444", width: 1 }, areaStyle: { color: "rgba(239,68,68,.08)" } },
    ],
  }), [t]);
  const folds = useMemo(() => t && ({
    grid: { left: 44, right: 44, top: 14, bottom: 28 },
    xAxis: { type: "category", data: t.folds.map((f) => f.start.slice(0, 7)), ...axisStyle },
    yAxis: [{ type: "value", name: "exp R", ...axisStyle }, { type: "value", min: 0, max: 1, name: "hit", ...axisStyle, splitLine: { show: false } }],
    tooltip: { trigger: "axis" }, legend: { textStyle: { color: "#7c8694" }, top: 0 },
    series: [
      { name: "OOS expectancy", type: "bar", data: t.folds.map((f) => ({ value: f.exp_r, itemStyle: { color: f.exp_r >= 0 ? "#22c55e" : "#ef4444" } })) },
      { name: "realised hit", type: "line", yAxisIndex: 1, data: t.folds.map((f) => f.hit), lineStyle: { color: "#60a5fa" } },
      { name: "predicted", type: "line", yAxisIndex: 1, data: t.folds.map((f) => f.pred), lineStyle: { color: "#a1a1aa", type: "dashed" } },
    ],
  }), [t]);
  const hist = useMemo(() => t && ({
    grid: { left: 36, right: 10, top: 20, bottom: 26 }, legend: { textStyle: { color: "#7c8694" }, top: 0 },
    xAxis: { type: "category", data: [...t.mae_hist_losers.map((b) => b.lo.toFixed(1)), ...t.mfe_hist_winners.map((b) => b.lo.toFixed(1))], ...axisStyle },
    yAxis: { type: "value", ...axisStyle },
    series: [
      { name: "MAE (losers)", type: "bar", stack: "x", data: [...t.mae_hist_losers.map((b) => b.n), ...t.mfe_hist_winners.map(() => 0)], itemStyle: { color: "#ef4444" } },
      { name: "MFE (winners)", type: "bar", stack: "x", data: [...t.mae_hist_losers.map(() => 0), ...t.mfe_hist_winners.map((b) => b.n)], itemStyle: { color: "#22c55e" } },
    ],
  }), [t]);
  const heat = useMemo(() => {
    if (!t?.sensitivity?.length) return null;
    const keys = Object.keys(t.sensitivity[0].params);
    if (keys.length !== 2) return null;
    const xs = Array.from(new Set(t.sensitivity.map((s) => s.params[keys[0]]))).sort((a, b) => +a - +b);
    const ys = Array.from(new Set(t.sensitivity.map((s) => s.params[keys[1]]))).sort((a, b) => +a - +b);
    const data = t.sensitivity.map((s) => [xs.indexOf(s.params[keys[0]]), ys.indexOf(s.params[keys[1]]), +s.exp_r.toFixed(3)]);
    const vals = data.map((d) => d[2] as number);
    return {
      grid: { left: 60, right: 10, top: 10, bottom: 40 },
      xAxis: { type: "category", data: xs, name: keys[0], nameLocation: "middle", nameGap: 24, ...axisStyle },
      yAxis: { type: "category", data: ys, name: keys[1], ...axisStyle },
      visualMap: { min: Math.min(...vals, -0.3), max: Math.max(...vals, 0.3), calculable: false, show: false, inRange: { color: ["#7f1d1d", "#1f2937", "#14532d"] } },
      series: [{ type: "heatmap", data, label: { show: true, color: "#d8dde4", fontFamily: "JetBrains Mono", fontSize: 11 } }],
    };
  }, [t]);
  if (!t) return <div className="text-mute">Loading tearsheet…</div>;
  return (
    <div className="space-y-3" data-testid="tearsheet">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {Object.entries(t.sides).map(([side, d]) => (
          <Panel key={side} title={`${t.label} · ${side.toUpperCase()}: in-sample vs out-of-sample`}>
            <div className={clsx("text-[12px] pb-2", d.verdict === "EDGE" ? "text-long" : d.verdict === "MARGINAL" ? "text-warn" : "text-short")}>{d.verdict}: {d.why}</div>
            <table className="w-full text-[12px]">
              <thead><tr className="text-mute text-left"><th className="font-normal" /><th className="font-normal text-right">N</th><th className="font-normal text-right">Hit</th><th className="font-normal text-right">Avg win</th><th className="font-normal text-right">Avg loss</th><th className="font-normal text-right">Exp R</th><th className="font-normal text-right">PF</th></tr></thead>
              <tbody>{(["is", "oos"] as const).map((k) => (
                <tr key={k} className="border-t hair"><td className="text-mute">{k === "is" ? "In-sample" : "Out-of-sample"}</td>
                  <td className="num text-right">{int(d[k].n)}</td><td className="num text-right">{pct(d[k].hit, 1)}</td>
                  <td className="num text-right">{r(d[k].win_r)}</td><td className="num text-right">{r(d[k].loss_r)}</td>
                  <td className={clsx("num text-right", (d[k].expectancy ?? 0) > 0 ? "text-long" : "text-short")}>{r(d[k].expectancy, 3)}</td>
                  <td className="num text-right">{isNum(d[k].profit_factor) ? (d[k].profit_factor as number).toFixed(2) : dash}</td></tr>
              ))}</tbody>
            </table>
            <div className="grid grid-cols-2 gap-x-4 text-[11.5px] pt-2">
              <div className="text-mute">DSR <span className="num text-ink">{isNum(d.dsr?.dsr) ? (d.dsr.dsr as number).toFixed(2) : dash}</span> (trials {d.dsr?.n_trials ?? dash}, SR/trade {isNum(d.dsr?.sr) ? (d.dsr.sr as number).toFixed(3) : dash})</div>
              <div className="text-mute">Monte Carlo max DD p50/p95 <span className="num text-ink">{isNum(d.mc?.dd_p50) ? `${(d.mc.dd_p50 as number).toFixed(1)} / ${(d.mc.dd_p95 as number).toFixed(1)}R` : dash}</span>, P(total ≤ 0) <span className="num text-ink">{pct(d.mc?.p_total_le_0)}</span></div>
            </div>
            <table className="w-full text-[11.5px] mt-2">
              <thead><tr className="text-mute text-left"><th className="font-normal">Regime bucket</th><th className="font-normal text-right">N</th><th className="font-normal text-right">Hit</th><th className="font-normal text-right">Exp</th><th className="font-normal text-right">Cons EV</th><th className="font-normal">Gate</th></tr></thead>
              <tbody>{d.regimes.map((x) => (
                <tr key={x.regime} className="border-t hair"><td>{x.regime}</td><td className="num text-right">{x.n}</td><td className="num text-right">{pct(x.hit, 1)}</td><td className="num text-right">{r(x.expectancy, 3)}</td><td className="num text-right">{r(x.cons_ev, 3)}</td>
                  <td className={x.qualified ? "text-long" : "text-mute"} title={x.reasons.join("; ")}>{x.qualified ? "pass" : x.reasons[0] ?? "fail"}</td></tr>
              ))}</tbody>
            </table>
          </Panel>
        ))}
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Panel title="OOS equity (R and ₹, net of costs) & drawdown" className="xl:col-span-2">{eq && <EChart option={eq as never} height={260} testid="equity" />}</Panel>
        <Panel title="Facts">
          <div className="text-[12px] space-y-1">
            <div>Avg win / loss: <span className="num">{r(t.avg_win_r)} / {r(t.avg_loss_r)}</span> (<span className="num">{rupees(t.avg_win_inr)} / {rupees(t.avg_loss_inr)}</span>)</div>
            <div>Avg all-in cost per trade: <span className="num">{rupees(t.avg_cost_inr)}</span></div>
            <div>Trades per day (OOS): <span className="num">{isNum(t.trades_per_day) ? t.trades_per_day.toFixed(2) : dash}</span></div>
            <div>Max drawdown (OOS): <span className="num">{isNum(t.max_dd_r) ? `${t.max_dd_r!.toFixed(1)}R` : dash}</span></div>
            <div>Exits: <span className="num">{Object.entries(t.exit_reasons).map(([k, v]) => `${k} ${v}`).join(" · ")}</span></div>
          </div>
        </Panel>
        <Panel title="Walk-forward folds: OOS expectancy, realised vs predicted hit" className="xl:col-span-2">{folds && <EChart option={folds as never} height={240} />}</Panel>
        <Panel title="Calibration plot (OOS)"><Reliability rows={t.calibration} /></Panel>
        <Panel title="MAE of losers / MFE of winners (R)" className="xl:col-span-2">{hist && <EChart option={hist as never} height={220} />}</Panel>
        <Panel title="Parameter sensitivity (full-sample exp R): prefer plateaus to peaks">
          {heat ? <EChart option={heat as never} height={220} testid="heatmap" /> : <div className="text-mute text-[12px]">Run <span className="num">dalal backtest --sensitivity</span> to fill the grid.</div>}
        </Panel>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        {Object.entries(t.breakdowns).map(([k, rows]) => (
          <Panel key={k} title={`By ${k.replace("_name", "")}`}>
            <table className="w-full text-[11.5px]"><tbody>{rows.map((b) => (
              <tr key={b.bucket} className="border-t hair"><td>{b.bucket}</td><td className="num text-right">{int(b.n)}</td><td className="num text-right">{pct(b.hit)}</td>
                <td className={clsx("num text-right", (b.expectancy ?? 0) > 0 ? "text-long" : "text-short")}>{r(b.expectancy, 3)}</td></tr>
            ))}</tbody></table>
          </Panel>
        ))}
      </div>
    </div>
  );
}

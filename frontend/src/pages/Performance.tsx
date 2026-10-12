import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { axisStyle, EChart } from "../components/EChart";
import { getJSON, postJSON } from "../lib/api";
import { dash, hhmm, int, isNum, pct, px, r, rupees, STATUS_LABEL } from "../lib/format";
import { Panel } from "./Market";

interface Bucket { bucket: string; n: number; hit: number; expectancy: number; pred: number; ci_lo: number; ci_hi: number }
interface Perf {
  n_shown: number; n_resolved: number; n_triggered?: number;
  hit_rate?: { k: number; n: number; hit: number; ci_lo: number; ci_hi: number; pred_mean: number };
  brier?: number; total_r?: number; max_dd_r?: number; expectancy_r?: number;
  reliability?: { pred: number; obs: number; obs_lo: number; obs_hi: number; n: number }[];
  equity?: { ts: string; cum_r: number }[];
  by_setup?: Bucket[]; by_regime?: Bucket[]; by_hour?: Bucket[]; by_side?: Bucket[];
  status_counts?: Record<string, number>;
  feedback?: { entries: Record<string, { live_n: number; live_hit: number; pred: number; p_value: number; live_exp_r: number; demoted: boolean; reason: string }> };
}
interface JournalResp {
  ideas: { id: string; day: string; signal_ts: string; symbol: string; side: string; setup: string; status: string; p: number | null; r_realised: number | null; gate_bypassed: number; regime: string; grade: string }[];
  paper: { id: number; idea_id: string; symbol: string; side: string; setup: string; qty: number; entry_px: number | null; exit_px: number | null; pnl_inr: number | null; r: number | null; status: string; taken_ts: string }[];
  manual: { id: number; day: string; symbol: string; side: string; qty: number; entry: number; exit: number | null; pnl_inr: number | null; r: number | null; notes: string }[];
}

export function Performance() {
  const [mode, setMode] = useState<"LIVE" | "REPLAY">("LIVE");
  const [demo, setDemo] = useState(false);
  const perf = useQuery({ queryKey: ["perf", mode, demo], queryFn: () => getJSON<Perf>(`/api/performance?mode=${mode}&include_demo=${demo}`), refetchInterval: 15000 });
  const jr = useQuery({ queryKey: ["journal", mode, demo], queryFn: () => getJSON<JournalResp>(`/api/journal?mode=${mode}&include_demo=${demo}`), refetchInterval: 15000 });
  const p = perf.data;
  const rel = useMemo(() => ({
    grid: { left: 40, right: 12, top: 12, bottom: 30 },
    xAxis: { type: "value", min: 0, max: 1, name: "predicted", nameLocation: "middle", nameGap: 20, ...axisStyle },
    yAxis: { type: "value", min: 0, max: 1, name: "observed", ...axisStyle },
    tooltip: { trigger: "item" },
    series: [
      { type: "line", data: [[0, 0], [1, 1]], showSymbol: false, lineStyle: { color: "#3f4654", type: "dashed" } },
      { type: "scatter", data: (p?.reliability ?? []).map((b) => [b.pred, b.obs, b.n]), symbolSize: (v: number[]) => Math.max(6, Math.min(24, Math.sqrt(v[2]) * 2)), itemStyle: { color: "#60a5fa" } },
      { type: "custom", renderItem: (_: unknown, api: { value: (i: number) => number; coord: (v: number[]) => number[] }) => {
          const x = api.value(0), lo = api.coord([x, api.value(1)]), hi = api.coord([x, api.value(2)]);
          return { type: "line", shape: { x1: lo[0], y1: lo[1], x2: hi[0], y2: hi[1] }, style: { stroke: "#60a5fa", lineWidth: 1 } };
        }, data: (p?.reliability ?? []).map((b) => [b.pred, b.obs_lo, b.obs_hi]) },
    ],
  }), [p?.reliability]);
  const eq = useMemo(() => ({
    grid: { left: 44, right: 12, top: 12, bottom: 24 },
    xAxis: { type: "category", data: (p?.equity ?? []).map((e) => e.ts.slice(0, 16).replace("T", " ")), ...axisStyle, axisLabel: { show: false } },
    yAxis: { type: "value", name: "cum R", ...axisStyle },
    tooltip: { trigger: "axis" },
    series: [{ type: "line", data: (p?.equity ?? []).map((e) => e.cum_r), showSymbol: false, lineStyle: { color: "#22c55e", width: 1.5 }, areaStyle: { color: "rgba(34,197,94,.08)" } }],
  }), [p?.equity]);
  return (
    <div className="p-3 space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        {(["LIVE", "REPLAY"] as const).map((m) => (
          <button key={m} onClick={() => setMode(m)} className={clsx("px-2 py-1 rounded border", mode === m ? "border-accent text-accent" : "hair text-mute")}>{m}</button>
        ))}
        <label className="flex items-center gap-1 text-mute"><input type="checkbox" checked={demo} onChange={(e) => setDemo(e.target.checked)} /> include demo (gate-bypassed) ideas</label>
        <span className="text-mute">The track record counts every idea shown, whether taken or not. Replay results on in-sample days are not forward evidence.</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2">
        <Kpi k="Shown" v={int(p?.n_shown)} />
        <Kpi k="Triggered" v={int(p?.n_triggered)} />
        <Kpi k="T1-first rate" v={p?.hit_rate ? `${pct(p.hit_rate.hit)} (${p.hit_rate.k}/${p.hit_rate.n})` : "insufficient data"} />
        <Kpi k="95% CI" v={p?.hit_rate ? `${pct(p.hit_rate.ci_lo)}–${pct(p.hit_rate.ci_hi)}` : dash} />
        <Kpi k="Predicted (mean)" v={pct(p?.hit_rate?.pred_mean)} />
        <Kpi k="Brier" v={isNum(p?.brier) ? (p!.brier as number).toFixed(3) : dash} />
        <Kpi k="Total / exp." v={`${r(p?.total_r, 1)} / ${r(p?.expectancy_r)}`} />
        <Kpi k="Max DD" v={isNum(p?.max_dd_r) ? `${(p!.max_dd_r as number).toFixed(1)}R` : dash} />
      </div>
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <Panel title="Reliability: forward hit rate vs predicted probability (95% CI)">
          {p?.reliability?.length ? <EChart option={rel as never} height={260} testid="reliability" /> : <Empty />}
        </Panel>
        <Panel title="Cumulative R (net of costs)" className="xl:col-span-2">
          {p?.equity?.length ? <EChart option={eq as never} height={260} /> : <Empty />}
        </Panel>
        <BucketTable title="Expectancy by setup" rows={p?.by_setup} />
        <BucketTable title="Expectancy by regime" rows={p?.by_regime} />
        <BucketTable title="Expectancy by time of day" rows={p?.by_hour} />
      </div>
      <Panel title="Live vs backtest divergence (auto-demotion)">
        {p?.feedback?.entries && Object.keys(p.feedback.entries).length ? (
          <table className="w-full text-[12px]"><thead><tr className="text-mute text-left"><th className="font-normal">Book entry</th><th className="font-normal text-right">Live N</th><th className="font-normal text-right">Live hit</th><th className="font-normal text-right">Predicted</th><th className="font-normal text-right">p-value</th><th className="font-normal">Status</th></tr></thead>
            <tbody>{Object.entries(p.feedback.entries).filter(([k]) => k.endsWith("|*")).map(([k, e]) => (
              <tr key={k} className="border-t hair"><td>{k.replace("|*", "")}</td><td className="num text-right">{e.live_n}</td><td className="num text-right">{pct(e.live_hit)}</td><td className="num text-right">{pct(e.pred)}</td><td className="num text-right">{e.p_value.toFixed(3)}</td>
                <td className={e.demoted ? "text-short" : "text-long"}>{e.demoted ? `DEMOTED: ${e.reason}` : "consistent"}</td></tr>
            ))}</tbody></table>
        ) : <div className="text-mute text-[12px]">No live feedback yet (built from LIVE sessions only).</div>}
      </Panel>
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <Panel title="Journal: every idea shown">
          <div className="max-h-[360px] overflow-auto scroll-thin">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-panel"><tr className="text-mute text-left"><th className="font-normal">Day</th><th className="font-normal">Time</th><th className="font-normal">Symbol</th><th className="font-normal">Setup</th><th className="font-normal">Status</th><th className="font-normal text-right">P</th><th className="font-normal text-right">R</th></tr></thead>
              <tbody>{(jr.data?.ideas ?? []).map((i) => (
                <tr key={i.id} className="border-t hair">
                  <td className="num text-mute">{i.day}</td><td className="num">{hhmm(i.signal_ts)}</td>
                  <td className={i.side === "long" ? "text-long" : "text-short"}>{i.symbol}{i.gate_bypassed ? <span className="text-warn text-[10px]"> demo</span> : null}</td>
                  <td className="text-mute">{i.setup}</td><td>{STATUS_LABEL[i.status] ?? i.status}</td>
                  <td className="num text-right">{pct(i.p)}</td><td className="num text-right">{r(i.r_realised)}</td>
                </tr>
              ))}</tbody>
            </table>
            {!jr.data?.ideas?.length && <Empty />}
          </div>
        </Panel>
        <Panel title="Paper trades & manual entries">
          <table className="w-full text-[12px]">
            <thead><tr className="text-mute text-left"><th className="font-normal">Taken</th><th className="font-normal">Symbol</th><th className="font-normal text-right">Qty</th><th className="font-normal text-right">Entry</th><th className="font-normal text-right">Exit</th><th className="font-normal text-right">P&L</th><th className="font-normal">Status</th></tr></thead>
            <tbody>{(jr.data?.paper ?? []).map((t) => (
              <tr key={t.id} className="border-t hair"><td className="num text-mute">{t.taken_ts.slice(0, 16).replace("T", " ")}</td><td>{t.symbol} <span className="text-mute">{t.side}</span></td>
                <td className="num text-right">{int(t.qty)}</td><td className="num text-right">{px(t.entry_px)}</td><td className="num text-right">{px(t.exit_px)}</td>
                <td className={clsx("num text-right", (t.pnl_inr ?? 0) >= 0 ? "text-long" : "text-short")}>{rupees(t.pnl_inr)}</td><td>{t.status}</td></tr>
            ))}
              {(jr.data?.manual ?? []).map((t) => (
                <tr key={`m${t.id}`} className="border-t hair"><td className="num text-mute">{t.day} (manual)</td><td>{t.symbol} <span className="text-mute">{t.side}</span></td>
                  <td className="num text-right">{int(t.qty)}</td><td className="num text-right">{px(t.entry)}</td><td className="num text-right">{px(t.exit)}</td>
                  <td className={clsx("num text-right", (t.pnl_inr ?? 0) >= 0 ? "text-long" : "text-short")}>{rupees(t.pnl_inr)}</td><td className="text-mute">{t.notes}</td></tr>
              ))}</tbody>
          </table>
          <ManualForm onDone={() => jr.refetch()} />
        </Panel>
      </div>
    </div>
  );
}

function Kpi({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="rounded-md border hair bg-panel px-2.5 py-2"><div className="label">{k}</div><div className="num text-[14px] pt-0.5">{v}</div></div>;
}
function Empty() { return <div className="text-mute text-[12px]">insufficient data</div>; }

function BucketTable({ title, rows }: { title: string; rows?: Bucket[] }) {
  return (
    <Panel title={title}>
      {rows?.length ? (
        <table className="w-full text-[12px]"><thead><tr className="text-mute text-left"><th className="font-normal">Bucket</th><th className="font-normal text-right">N</th><th className="font-normal text-right">Hit</th><th className="font-normal text-right">Pred</th><th className="font-normal text-right">Exp R</th></tr></thead>
          <tbody>{rows.map((b) => (
            <tr key={b.bucket} className="border-t hair"><td>{b.bucket}</td><td className="num text-right">{b.n}</td><td className="num text-right">{pct(b.hit)}</td><td className="num text-right text-mute">{pct(b.pred)}</td>
              <td className={clsx("num text-right", b.expectancy >= 0 ? "text-long" : "text-short")}>{r(b.expectancy)}</td></tr>
          ))}</tbody></table>
      ) : <Empty />}
    </Panel>
  );
}

function ManualForm({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ day: new Date().toISOString().slice(0, 10), symbol: "", side: "long", qty: "", entry: "", stop: "", exit: "", notes: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const inp = "bg-panel2 border hair rounded px-1.5 py-0.5 num w-full";
  return (
    <form className="pt-3 grid grid-cols-4 gap-1.5 text-[11.5px]" onSubmit={async (e) => {
      e.preventDefault();
      try {
        await postJSON("/api/manual", { ...f, qty: +f.qty, entry: +f.entry, stop: f.stop ? +f.stop : null, exit: f.exit ? +f.exit : null });
        setMsg("saved");
        onDone();
      } catch (err) { setMsg((err as Error).message); }
    }}>
      <div className="col-span-4 label">Manual entry</div>
      <input className={inp} value={f.day} onChange={set("day")} placeholder="YYYY-MM-DD" />
      <input className={inp} value={f.symbol} onChange={set("symbol")} placeholder="Symbol" required />
      <select className={inp} value={f.side} onChange={set("side")}><option value="long">long</option><option value="short">short</option></select>
      <input className={inp} value={f.qty} onChange={set("qty")} placeholder="Qty" required />
      <input className={inp} value={f.entry} onChange={set("entry")} placeholder="Entry" required />
      <input className={inp} value={f.stop} onChange={set("stop")} placeholder="Stop" />
      <input className={inp} value={f.exit} onChange={set("exit")} placeholder="Exit" />
      <input className={inp} value={f.notes} onChange={set("notes")} placeholder="Notes" />
      <button className="col-span-1 px-2 py-1 rounded border border-accent/50 text-accent">Add</button>
      {msg && <span className="col-span-3 text-mute self-center">{msg}</span>}
    </form>
  );
}

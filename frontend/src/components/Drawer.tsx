import clsx from "clsx";
import { useEffect, useMemo, useState } from "react";
import { getJSON, postJSON } from "../lib/api";
import { dash, hhmm, hhmmss, int, isNum, pct, px, r, rupees, STATUS_LABEL, statusTone } from "../lib/format";
import type { IdeaCard } from "../lib/types";
import { allBoardCards, useTerminal } from "../store/terminal";
import { type Bar, CandleChart, type Level } from "./CandleChart";

interface Detail {
  card: IdeaCard;
  stats: Record<string, number | string | boolean | string[] | null> | null;
  similar: { day: string; time: string; symbol: string; regime: string; win: boolean; r_net: number; exit: string; distance: number }[];
}

function Stat({ k, v, tone }: { k: string; v: React.ReactNode; tone?: string }) {
  return (
    <div className="flex justify-between gap-2 py-0.5 border-b hair last:border-0">
      <span className="text-mute text-[11.5px]">{k}</span>
      <span className={clsx("num text-[12px]", tone)}>{v}</span>
    </div>
  );
}

export function Drawer() {
  const { drawerId, board, clock, set } = useTerminal();
  const [tf, setTf] = useState(5);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const card = useMemo(() => [...allBoardCards(board), ...(board?.resolved ?? [])].find((c) => c.id === drawerId) ?? detail?.card ?? null,
    [board, drawerId, detail]);
  useEffect(() => {
    if (!drawerId) return;
    setDetail(null);
    setMsg(null);
    getJSON<Detail>(`/api/idea/${encodeURIComponent(drawerId)}`).then(setDetail).catch((e) => setMsg(String(e)));
  }, [drawerId]);
  const minuteKey = clock?.slice(11, 16);
  useEffect(() => {
    if (!card) return;
    getJSON<{ bars: Bar[] }>(`/api/candles?key=${encodeURIComponent(card.key)}&day=${card.day}&tf=${tf}`)
      .then((d) => setBars(d.bars)).catch(() => setBars([]));
  }, [card?.key, card?.day, tf, tf === 1 ? minuteKey : minuteKey?.slice(0, 4)]);  // eslint-disable-line react-hooks/exhaustive-deps
  const levels: Level[] = useMemo(() => card ? [
    { price: card.entry, color: "#60a5fa", title: "Entry" },
    { price: card.stop, color: "#ef4444", title: "Stop", dashed: true },
    { price: card.t1, color: "#22c55e", title: "T1", dashed: true },
    { price: card.t2, color: "#16a34a", title: "T2", dashed: true },
  ] : [], [card?.entry, card?.stop, card?.t1, card?.t2]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!drawerId) return null;
  const s = detail?.stats;
  const n = (x: unknown) => (typeof x === "number" ? x : null);
  return (
    <div className="fixed inset-0 z-40 flex justify-end" data-testid="drawer">
      <div className="absolute inset-0 bg-black/50" onClick={() => set({ drawerId: null })} />
      <aside className="relative w-full max-w-[980px] h-full bg-panel border-l hair overflow-y-auto scroll-thin animate-slidein">
        {!card ? (
          <div className="p-4 text-mute">{msg ?? "Loading…"}</div>
        ) : (
          <div className="p-4 space-y-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="flex items-baseline gap-2">
                  <h3 className="text-[18px] font-semibold tracking-wide">{card.symbol}</h3>
                  <span className={clsx("font-semibold", card.side === "long" ? "text-long" : "text-short")}>{card.side.toUpperCase()}</span>
                  <span className={clsx("text-[11px] px-1.5 py-0.5 rounded border", statusTone(card.status))}>{STATUS_LABEL[card.status]}</span>
                  <span className="num text-[11px] px-1.5 py-0.5 rounded border hair">{card.grade}</span>
                </div>
                <div className="text-mute text-[12px]">{card.sector} · {card.setup_label} · regime {card.regime.replace(/_/g, " ")} · signal {hhmm(card.signal_ts)} · valid to {hhmm(card.valid_until)}</div>
              </div>
              <div className="flex items-center gap-2">
                <PaperButton card={card} />
                <button className="px-2 py-1 rounded border hair text-mute" onClick={() => set({ drawerId: null })}>Close <kbd>Esc</kbd></button>
              </div>
            </div>
            {card.gate_bypassed && (
              <div className="ribbon-demo rounded px-3 py-1.5 text-[12px]">
                GATE BYPASSED (synthetic demo). This setup did NOT pass the live-board gate: {card.gate_reasons.join("; ")}
              </div>
            )}
            <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-3">
              <div className="rounded border hair p-2">
                <div className="flex items-center gap-1 pb-2">
                  {[1, 5, 15].map((x) => (
                    <button key={x} onClick={() => setTf(x)} className={clsx("px-2 py-0.5 rounded text-[11px] num", tf === x ? "bg-accent/20 text-accent" : "text-mute")}>{x}m</button>
                  ))}
                  <span className="ml-auto text-[10.5px] text-mute">VWAP (dotted) · EMA20 (blue) · entry/stop/T1/T2 lines · bars seen so far</span>
                </div>
                <CandleChart bars={bars} levels={levels} />
                <div className="text-[10.5px] text-mute pt-1">
                  Futures OI: {card.snapshot["oi_state_text"] ? String(card.snapshot["oi_state_text"]) : "not available from this data source"} ·
                  Charts by <a className="underline" href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a> Lightweight Charts™
                </div>
              </div>
              <div className="space-y-3">
                <div className="rounded border hair p-2">
                  <div className="label pb-1">Plan</div>
                  <Stat k="Entry (stop-entry)" v={`${px(card.entry_zone[0])}–${px(card.entry_zone[1])}`} />
                  <Stat k="Stop" v={px(card.stop)} tone="text-short" />
                  <Stat k="T1 / T2" v={`${px(card.t1)} / ${px(card.t2)}`} tone="text-long" />
                  <Stat k="R:R" v={`1:${card.rr1.toFixed(2)} / 1:${card.rr2.toFixed(2)}`} />
                  <Stat k="Qty · ₹ risk" v={`${int(card.qty)} · ${rupees(card.risk_inr)}`} />
                  <Stat k="Notional" v={rupees(card.notional)} />
                  {isNum(card.entry_px) && <Stat k="Filled at" v={px(card.entry_px)} />}
                  {isNum(card.r_realised) && <Stat k="Result (net)" v={r(card.r_realised)} />}
                </div>
                <div className="rounded border hair p-2" data-testid="stats">
                  <div className="label pb-1">Evidence ({card.stats_level === "*" ? "pooled across regimes" : `regime ${card.stats_level}`})</div>
                  <Stat k="Calibrated P(T1 first)" v={pct(card.p, 1)} />
                  <Stat k="95% CI (Wilson)" v={`${pct(card.ci_lo, 1)} – ${pct(card.ci_hi, 1)}`} />
                  <Stat k="OOS instances N" v={int(card.n)} />
                  <Stat k="Raw OOS hit rate" v={pct(n(s?.hit), 1)} />
                  <Stat k="Avg win / loss (net R)" v={`${r(card.win_r)} / ${r(card.loss_r)}`} />
                  <Stat k="Expectancy (net)" v={r(card.expectancy)} />
                  <Stat k="Breakeven win rate" v={pct(card.breakeven, 1)} />
                  <Stat k="Conservative EV" v={r(card.cons_ev)} tone={isNum(card.cons_ev) && (card.cons_ev as number) > 0 ? "text-long" : "text-short"} />
                  <Stat k="Profit factor" v={isNum(n(s?.profit_factor)) ? (n(s?.profit_factor) as number).toFixed(2) : dash} />
                  <Stat k="Walk-forward stability" v={pct(n(s?.stability))} />
                  <Stat k="Deflated Sharpe (prob.)" v={isNum(n(s?.dsr)) ? (n(s?.dsr) as number).toFixed(2) : dash} />
                  <Stat k="Live forward hit" v={card.live_n ? `${pct(card.live_hit)} (n ${card.live_n})` : "not yet"} />
                  <div className="text-[10.5px] text-mute pt-1">model {card.model_version} · config {card.config_hash} · {card.provenance}</div>
                </div>
              </div>
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <div className="rounded border hair p-2">
                <div className="label pb-1">Thesis</div>
                <ul className="space-y-1 text-[12.5px]">{card.thesis.map((t, i) => <li key={i}>• {t}</li>)}</ul>
                <div className="text-[12px] text-mute pt-2"><span className="text-warn">Invalidation:</span> {card.invalidation}</div>
                <div className="label pt-3 pb-1">Timeline</div>
                <ul className="text-[11.5px] num space-y-0.5">
                  {(card.events ?? []).map((e, i) => (
                    <li key={i}><span className="text-mute">{hhmmss(e.ts)}</span> {STATUS_LABEL[e.status] ?? e.status} {isNum(e.price) ? `@ ${px(e.price)}` : ""} <span className="text-mute">{e.note}</span></li>
                  ))}
                </ul>
              </div>
              <div className="rounded border hair p-2">
                <div className="label pb-1">5 most similar past OOS instances</div>
                {detail?.similar?.length ? (
                  <table className="w-full text-[12px]">
                    <thead><tr className="text-mute text-left"><th className="font-normal">Day</th><th className="font-normal">Symbol</th><th className="font-normal">Regime</th><th className="font-normal">Exit</th><th className="font-normal text-right">Net R</th></tr></thead>
                    <tbody>
                      {detail.similar.map((x, i) => (
                        <tr key={i} className="border-t hair">
                          <td className="num py-0.5">{x.day} {x.time}</td><td>{x.symbol}</td><td className="text-mute">{x.regime}</td><td>{x.exit}</td>
                          <td className={clsx("num text-right", x.win ? "text-long" : "text-short")}>{r(x.r_net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <div className="text-mute text-[12px]">{detail ? "insufficient data" : "…"}</div>}
                <div className="label pt-3 pb-1">Feature snapshot at signal</div>
                <div className="grid grid-cols-2 gap-x-3 text-[11.5px]">
                  {Object.entries(card.snapshot).filter(([, v]) => typeof v === "number").slice(0, 16).map(([k, v]) => (
                    <div key={k} className="flex justify-between border-b hair"><span className="text-mute">{k}</span><span className="num">{(v as number).toFixed(2)}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </aside>
    </div>
  );
}

function PaperButton({ card }: { card: IdeaCard }) {
  const [state, setState] = useState<string | null>(card.paper ? "taken" : null);
  return (
    <button disabled={state === "taken"} data-testid="take-trade"
      className={clsx("px-2.5 py-1 rounded border text-[12px]", state === "taken" ? "border-line text-mute" : "border-accent/60 text-accent hover:bg-accent/10")}
      onClick={async () => {
        try {
          await postJSON(`/api/paper/${encodeURIComponent(card.id)}`, {});
          setState("taken");
        } catch (e) {
          setState(String((e as Error).message));
        }
      }}>
      {state === "taken" ? "Paper trade taken" : state ? state : "Take paper trade"}
    </button>
  );
}

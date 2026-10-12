import clsx from "clsx";
import { memo, useEffect, useRef } from "react";
import { dash, hhmm, int, isNum, pct, pctRaw, px, r, rupees, STATUS_LABEL, statusTone } from "../lib/format";
import type { IdeaCard as Card } from "../lib/types";
import { useTerminal } from "../store/terminal";

export function Sparkline({ c, vwap, side, w = 132, h = 34 }: { c: (number | null)[]; vwap: (number | null)[]; side: string; w?: number; h?: number }) {
  const vals = [...c, ...vwap].filter((x): x is number => isNum(x));
  if (vals.length < 2) return <div style={{ width: w, height: h }} className="text-mute text-[10px] flex items-center">—</div>;
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const n = 75;
  const x = (i: number) => (i / (n - 1)) * w;
  const y = (v: number) => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
  const path = (a: (number | null)[]) =>
    a.map((v, i) => (isNum(v) ? `${i === 0 || !isNum(a[i - 1]) ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}` : "")).join("");
  return (
    <svg width={w} height={h} className="block" aria-label="intraday sparkline with VWAP">
      <path d={path(vwap)} fill="none" stroke="var(--mute)" strokeWidth={1} strokeDasharray="2 2" />
      <path d={path(c)} fill="none" stroke={side === "long" ? "var(--long)" : "var(--short)"} strokeWidth={1.4} />
    </svg>
  );
}

function Price({ k, fallback }: { k: string; fallback: number | null }) {
  const p = useTerminal((s) => s.prices[k]);
  const prev = useTerminal((s) => s.prevPrices[k]);
  const ref = useRef<HTMLSpanElement>(null);
  const v = p ?? fallback;
  useEffect(() => {
    const el = ref.current;
    if (!el || p === undefined || prev === undefined || p === prev) return;
    el.classList.remove("animate-flashup", "animate-flashdn");
    void el.offsetWidth;
    el.classList.add(p > prev ? "animate-flashup" : "animate-flashdn");
  }, [p, prev]);
  return <span ref={ref} className="num px-0.5 rounded">{px(v)}</span>;
}

function Row({ k, v, tone }: { k: string; v: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="label">{k}</span>
      <span className={clsx("num text-[12.5px]", tone)}>{v}</span>
    </div>
  );
}

export const IdeaCard = memo(function IdeaCard({ c, selected, fresh, onOpen }: { c: Card; selected: boolean; fresh: boolean; onOpen: () => void }) {
  const long = c.side === "long";
  const price = useTerminal((s) => s.prices[c.key]) ?? c.last_price;
  const dist = isNum(price) ? ((c.entry - (price as number)) / (price as number)) * 100 * (long ? 1 : -1) : null;
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  return (
    <div ref={ref} role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === "Enter" && onOpen()}
      data-testid="idea-card" data-side={c.side} data-status={c.status}
      className={clsx(
        "relative rounded-md border bg-panel2/70 hover:bg-panel2 transition-colors cursor-pointer overflow-hidden",
        selected ? "border-accent" : "hair",
        fresh && (long ? "animate-glowlong animate-slidein" : "animate-glowshort animate-slidein"),
      )}>
      <div className={clsx("absolute left-0 top-0 bottom-0 w-[3px]", long ? "bg-long" : "bg-short")} />
      {c.gate_bypassed && (
        <div className="ribbon-demo text-[10px] font-semibold tracking-wide px-3 py-0.5" title={c.gate_reasons.join("; ")}>
          GATE BYPASSED: PLUMBING DEMO (not a trade idea)
        </div>
      )}
      <div className="pl-3.5 pr-3 py-2.5 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <span className="font-semibold text-[14px] tracking-wide">{c.symbol}</span>
              <span className={clsx("text-[11px] font-semibold", long ? "text-long" : "text-short")}>{long ? "LONG" : "SHORT"}</span>
              <Price k={c.key} fallback={c.last_price} />
            </div>
            <div className="text-[11px] text-mute truncate">{c.sector} · {c.setup_label} · {c.regime.replace(/_/g, " ")}</div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className={clsx("num text-[11px] px-1.5 py-0.5 rounded border font-semibold",
              c.grade === "A+" ? "text-long border-long/50" : c.grade === "A" ? "text-accent border-accent/50" : "text-mute border-line")}>
              {c.grade}
            </span>
            <span className={clsx("text-[10.5px] px-1.5 py-0.5 rounded border", statusTone(c.status))} data-testid="status">
              {STATUS_LABEL[c.status] ?? c.status}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
          <Row k="Entry" v={`${px(c.entry_zone[0])}–${px(c.entry_zone[1])}`} />
          <Row k="Stop" v={px(c.stop)} tone="text-short" />
          <Row k="T1 / T2" v={`${px(c.t1)} / ${px(c.t2)}`} tone="text-long" />
          <Row k="R:R" v={`1:${c.rr1.toFixed(1)} / 1:${c.rr2.toFixed(1)}`} />
        </div>

        <div className="rounded border hair bg-bg/40 px-2 py-1.5 grid grid-cols-3 gap-2">
          <div>
            <div className="label">P(T1 first)</div>
            <div className="num text-[15px] text-ink" data-testid="prob">{pct(c.p)}</div>
            <div className="num text-[10.5px] text-mute">[{pct(c.ci_lo)}–{pct(c.ci_hi)}] N {int(c.n)}</div>
          </div>
          <div>
            <div className="label">Cons. EV</div>
            <div className={clsx("num text-[15px]", isNum(c.cons_ev) && (c.cons_ev as number) > 0 ? "text-long" : "text-short")}>{r(c.cons_ev)}</div>
            <div className="num text-[10.5px] text-mute">exp {r(c.expectancy)}</div>
          </div>
          <div>
            <div className="label">Size</div>
            <div className="num text-[15px]">{int(c.qty)}</div>
            <div className="num text-[10.5px] text-mute">risk {rupees(c.risk_inr)}</div>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2">
          <div className="text-[11px] text-mute num">
            {hhmm(c.signal_ts)} → valid {hhmm(c.valid_until)}
            <span className="ml-2">to entry <span className={clsx(isNum(dist) && Math.abs(dist as number) < 0.15 ? "text-warn" : "text-ink")}>{pctRaw(dist)}</span></span>
            {c.live_n > 0 && <span className="ml-2">live hit {pct(c.live_hit)} (n {c.live_n})</span>}
          </div>
          {c.spark ? <Sparkline c={c.spark.c} vwap={c.spark.vwap} side={c.side} /> : null}
        </div>

        <ul className="space-y-0.5 text-[12px] leading-snug">
          {c.thesis.map((t, i) => (
            <li key={i} className="flex gap-1.5"><span className={long ? "text-long" : "text-short"}>•</span><span>{t}</span></li>
          ))}
        </ul>
        <div className="text-[11.5px] text-mute border-t hair pt-1.5"><span className="text-warn">✕</span> {c.invalidation || dash}</div>
        {isNum(c.r_realised) && <div className="num text-[11.5px]">Result {r(c.r_realised)}</div>}
      </div>
    </div>
  );
});

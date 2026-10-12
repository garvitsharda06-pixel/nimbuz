import { ColorType, createChart, CrosshairMode, type IChartApi, LineStyle, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";

export type Bar = [number, number, number, number, number, number]; // unix s, o, h, l, c, v
export interface Level { price: number; color: string; title: string; dashed?: boolean }

const IST_OFFSET = 19800; // display exchange time (IST) on a UTC-based axis

function ema(vals: number[], n: number): number[] {
  const a = 2 / (n + 1);
  const out: number[] = [];
  vals.forEach((v, i) => out.push(i === 0 ? v : a * v + (1 - a) * out[i - 1]));
  return out;
}

export function CandleChart({ bars, levels, height = 340 }: { bars: Bar[]; levels: Level[]; height?: number }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      height,
      layout: { background: { type: ColorType.Solid, color: "#0d0f13" }, textColor: "#7c8694", fontFamily: "JetBrains Mono", fontSize: 11, attributionLogo: true },
      grid: { vertLines: { color: "#161a21" }, horzLines: { color: "#161a21" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#1c2028" },
      timeScale: { borderColor: "#1c2028", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    chart.current = c;
    return () => { c.remove(); chart.current = null; };
  }, [height]);

  useEffect(() => {
    const c = chart.current;
    if (!c) return;
    const cs = c.addCandlestickSeries({ upColor: "#22c55e", downColor: "#ef4444", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#ef4444" });
    const vol = c.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", color: "#2a3140" });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const vw = c.addLineSeries({ color: "#a1a1aa", lineWidth: 1, lineStyle: LineStyle.Dotted, priceLineVisible: false, lastValueVisible: false, title: "VWAP" });
    const e20 = c.addLineSeries({ color: "#60a5fa", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: "EMA20" });
    const t = (b: Bar) => (b[0] + IST_OFFSET) as UTCTimestamp;
    cs.setData(bars.map((b) => ({ time: t(b), open: b[1], high: b[2], low: b[3], close: b[4] })));
    vol.setData(bars.map((b) => ({ time: t(b), value: b[5], color: b[4] >= b[1] ? "rgba(34,197,94,.35)" : "rgba(239,68,68,.35)" })));
    let pv = 0, v = 0;
    vw.setData(bars.map((b) => {
      const tp = (b[2] + b[3] + b[4]) / 3;
      pv += tp * (b[5] || 1);
      v += b[5] || 1;
      return { time: t(b), value: pv / v };
    }));
    const em = ema(bars.map((b) => b[4]), 20);
    e20.setData(bars.map((b, i) => ({ time: t(b), value: em[i] })));
    levels.forEach((l) => cs.createPriceLine({ price: l.price, color: l.color, lineWidth: 1, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: l.title }));
    c.timeScale().fitContent();
    return () => {
      if (chart.current !== c) return;            // chart already disposed (unmount)
      [cs, vol, vw, e20].forEach((s) => { try { c.removeSeries(s); } catch { /* disposed */ } });
    };
  }, [bars, levels]);

  return <div ref={el} className="w-full" style={{ height }} data-testid="chart" />;
}

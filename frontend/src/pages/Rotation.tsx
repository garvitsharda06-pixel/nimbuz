import { useMemo } from "react";
import { axisStyle, EChart } from "../components/EChart";
import { useTerminal } from "../store/terminal";

// Intraday relative-rotation view: x = sector return minus Nifty (relative strength, %),
// y = change in that spread over the last 30 minutes (momentum). Quadrants: leading /
// weakening / lagging / improving. Built only from numbers the engine computed.
export function Rotation() {
  const m = useTerminal((s) => s.market);
  const opt = useMemo(() => {
    const pts = Object.entries(m?.sector_rs_series ?? {}).flatMap(([name, h]) => {
      const vals = h.filter((x): x is number => x !== null);
      if (!vals.length) return [];
      const last = vals[vals.length - 1];
      const prev = vals[Math.max(0, vals.length - 7)];        // 6 bars = 30 minutes
      const trail = vals.slice(-7).map((v, i, a) => [v, v - a[Math.max(0, i - 6)]]);
      return [{ name, value: [last, last - prev], trail }];
    });
    return {
      grid: { left: 44, right: 16, top: 16, bottom: 34 },
      xAxis: { type: "value", name: "RS vs Nifty %", nameLocation: "middle", nameGap: 22, ...axisStyle, nameTextStyle: { color: "#7c8694" } },
      yAxis: { type: "value", name: "30-min momentum", ...axisStyle, nameTextStyle: { color: "#7c8694" } },
      tooltip: { formatter: (p: { name: string; value: number[] }) => `${p.name}: RS ${p.value[0].toFixed(2)}%, mom ${p.value[1].toFixed(2)}` },
      series: [{
        type: "scatter", symbolSize: 11, data: pts.map((p) => ({ name: p.name, value: p.value })),
        label: { show: true, formatter: "{b}", position: "right", color: "#d8dde4", fontSize: 10 },
        itemStyle: { color: (p: { value: number[] }) => (p.value[0] >= 0 ? (p.value[1] >= 0 ? "#22c55e" : "#f59e0b") : p.value[1] >= 0 ? "#60a5fa" : "#ef4444") },
        markLine: { silent: true, symbol: "none", lineStyle: { color: "#3f4654" }, data: [{ xAxis: 0 }, { yAxis: 0 }] },
      }],
    };
  }, [m?.sector_rs_series]);
  return (
    <div>
      <EChart option={opt as never} height={300} testid="rrg" />
      <div className="text-[10.5px] text-mute">Green leading · amber weakening · red lagging · blue improving. x = sector return − Nifty return today; y = change of that spread over 30 min.</div>
    </div>
  );
}

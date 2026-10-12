import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export function EChart({ option, height = 280, testid }: { option: echarts.EChartsOption; height?: number; testid?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!el.current) return;
    inst.current = echarts.init(el.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(el.current);
    return () => { ro.disconnect(); inst.current?.dispose(); inst.current = null; };
  }, []);
  useEffect(() => {
    inst.current?.setOption(option, true);
  }, [option]);
  return <div ref={el} style={{ height }} className="w-full" data-testid={testid} />;
}

export const axisStyle = {
  axisLine: { lineStyle: { color: "#1c2028" } },
  axisLabel: { color: "#7c8694", fontFamily: "JetBrains Mono", fontSize: 10 },
  splitLine: { lineStyle: { color: "#161a21" } },
};

import clsx from "clsx";
import { useEffect, useState } from "react";
import { postJSON } from "../lib/api";
import { enableNotifications } from "../lib/alerts";
import { dash, hhmmss, isNum, pctRaw, regimeLabel, regimeTone } from "../lib/format";
import type { Quote } from "../lib/types";
import { useTerminal } from "../store/terminal";

function Tick({ label, q, digits = 2 }: { label: string; q?: Quote | null; digits?: number }) {
  const up = isNum(q?.chg_pct) ? (q!.chg_pct as number) >= 0 : null;
  return (
    <div className="flex items-baseline gap-1.5 whitespace-nowrap">
      <span className="label">{label}</span>
      <span className="num text-ink">{isNum(q?.ltp) ? (q!.ltp as number).toFixed(digits) : dash}</span>
      <span className={clsx("num text-[11px]", up === null ? "text-mute" : up ? "text-long" : "text-short")}>
        {pctRaw(q?.chg_pct ?? null)}
      </span>
    </div>
  );
}

export function TopBar({ tab, setTab }: { tab: string; setTab: (t: string) => void }) {
  const { status, health, market, clock, connected, muted, set } = useTerminal();
  const [wall, setWall] = useState(new Date());
  const [notif, setNotif] = useState(typeof Notification !== "undefined" && Notification.permission === "granted");
  useEffect(() => {
    const id = setInterval(() => setWall(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const replay = status?.mode === "REPLAY";
  const shownClock = replay || status?.mode === "IDLE"
    ? hhmmss(clock ?? status?.clock)
    : wall.toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
  const hstate = !connected ? "OFFLINE" : health?.state ?? "INIT";
  const hcol = hstate === "OK" ? "bg-long" : hstate === "INIT" ? "bg-mute" : hstate === "DEGRADED" ? "bg-warn" : "bg-short";
  const kill = !!status?.kill_switch;
  const tabs = ["Board", "Market", "Performance", "Lab", "Notes", "Settings"];
  return (
    <header className="relative z-20 border-b hair bg-panel">
      <div className="flex items-center gap-3 px-3 py-2 overflow-x-auto scroll-thin">
        <div className="flex items-center gap-2 pr-2 border-r hair">
          <span className="font-semibold tracking-wide">DALAL</span>
          <span className="text-mute">DESK</span>
        </div>
        <div className="flex items-center gap-2 whitespace-nowrap">
          <span className="num text-[15px] text-ink" data-testid="clock">{shownClock}</span>
          <span className="label">IST</span>
          <span className={clsx("px-1.5 py-0.5 rounded border text-[10.5px] font-medium", replay ? "text-accent border-accent/40" : status?.mode === "LIVE" ? "text-long border-long/40" : "text-mute border-line")}
            data-testid="mode">
            {status?.mode ?? "…"}{replay && status?.replay_day ? ` ${status.replay_day} · ${status.speed}×` : ""}
          </span>
          {status?.synthetic && (
            <span className="px-1.5 py-0.5 rounded border border-warn/50 text-warn text-[10.5px] font-medium" title="Synthetic data: plumbing/demo only, not market data">
              SYNTHETIC
            </span>
          )}
          <span className="text-mute text-[11px]">{status?.market_status && status.mode === "LIVE" ? status.market_status : ""}</span>
        </div>
        <Tick label="NIFTY" q={market?.nifty} />
        <Tick label="BANKNIFTY" q={market?.banknifty} />
        <Tick label="VIX" q={market?.vix} />
        <Tick label="USDINR" q={market?.usdinr} digits={3} />
        <div className="flex items-baseline gap-1.5 whitespace-nowrap">
          <span className="label">A/D</span>
          <span className="num">{isNum(market?.adv_pct) ? `${market!.adv_pct}% adv` : dash}</span>
        </div>
        <div className="flex items-baseline gap-1.5 whitespace-nowrap">
          <span className="label">&gt;VWAP</span>
          <span className="num">{isNum(market?.breadth_pct) ? `${market!.breadth_pct}%` : dash}</span>
        </div>
        <span className={clsx("px-2 py-0.5 rounded border text-[11px] font-medium whitespace-nowrap", regimeTone(market?.regime))} data-testid="regime">
          {regimeLabel(market?.regime)}
        </span>
        <div className="flex items-center gap-1.5 whitespace-nowrap" title={(health?.reasons ?? []).join("; ")}>
          <span className={clsx("w-2 h-2 rounded-full", hcol)} />
          <span className="num text-[11px] text-mute" data-testid="health">
            {hstate}{isNum(health?.latency_ms) ? ` ${Math.round(health!.latency_ms as number)}ms` : ""}
          </span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button className={clsx("px-2 py-1 rounded border text-[11px]", notif ? "border-line text-mute" : "border-accent/50 text-accent")}
            onClick={async () => setNotif(await enableNotifications())} title="Browser notifications when the tab is hidden">
            {notif ? "Alerts on" : "Enable alerts"}
          </button>
          <button className="px-2 py-1 rounded border hair text-[11px] text-mute" onClick={() => set({ muted: !muted })} title="Space">
            {muted ? "Muted" : "Sound"} <kbd>Space</kbd>
          </button>
          <button data-testid="kill"
            className={clsx("px-2.5 py-1 rounded border text-[11px] font-semibold tracking-wide", kill ? "bg-short/20 border-short text-short" : "border-short/50 text-short hover:bg-short/10")}
            onClick={() => postJSON("/api/kill", { on: !kill })}
            title="Kill switch: stop all NEW ideas (existing ideas keep being tracked)">
            {kill ? "KILL ON" : "KILL"}
          </button>
        </div>
      </div>
      <nav className="flex gap-1 px-2 border-t hair overflow-x-auto scroll-thin">
        {tabs.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={clsx("px-3 py-1.5 text-[12px] border-b-2 -mb-px whitespace-nowrap", tab === t ? "border-accent text-ink" : "border-transparent text-mute hover:text-ink")}>
            {t}
          </button>
        ))}
        <div className="ml-auto hidden md:flex items-center gap-3 text-[10.5px] text-mute pr-2">
          <span><kbd>J</kbd>/<kbd>K</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>/</kbd> search</span>
        </div>
      </nav>
      {(health && !health.signals_allowed && status?.mode !== "IDLE" && health.state !== "OK") && (
        <div className="bg-short/15 border-t border-short/40 text-short px-3 py-1 text-[12px]" data-testid="stale-banner">
          DATA FEED {health.state}: new signals suspended. {health.reasons.join("; ")}
        </div>
      )}
      {kill && (
        <div className="bg-short/15 border-t border-short/40 text-short px-3 py-1 text-[12px]">
          KILL SWITCH ON: no new ideas. Open ideas are still tracked.
        </div>
      )}
      {status?.mode === "IDLE" && <IdleStrip />}
      <ReplayStrip />
    </header>
  );
}

function IdleStrip() {
  return (
    <div className="px-3 py-1 text-[12px] text-mute border-t hair">
      Session idle. Start a replay below, or start LIVE from Settings (needs an Upstox token and real data).
    </div>
  );
}

function ReplayStrip() {
  const { status, set } = useTerminal();
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState<string>("");
  const [speed, setSpeed] = useState(30);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/days").then((r) => r.json()).then((d) => {
      const ds = (d.days as string[]).slice().reverse();
      setDays(ds);
      setDay(ds[0] ?? "");
    }).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (status?.replay_day) setDay(status.replay_day);
    if (status?.speed) setSpeed(status.speed);
  }, [status?.replay_day, status?.speed]);
  if (status?.mode === "LIVE") return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 border-t hair text-[12px]" data-testid="replay-strip">
      <span className="label">Replay</span>
      <select className="bg-panel2 border hair rounded px-1.5 py-0.5 num" value={day} onChange={(e) => setDay(e.target.value)} data-testid="replay-day">
        {days.map((d) => <option key={d} value={d}>{d}</option>)}
      </select>
      <select className="bg-panel2 border hair rounded px-1.5 py-0.5 num" value={speed}
        onChange={(e) => {
          const s = +e.target.value;
          setSpeed(s);
          if (status?.mode === "REPLAY") postJSON("/api/replay/control", { speed: s });
        }}>
        {[1, 5, 10, 30, 60].map((s) => <option key={s} value={s}>{s}×</option>)}
      </select>
      <button className="px-2 py-0.5 rounded border border-accent/50 text-accent" data-testid="replay-start"
        onClick={async () => {
          setErr(null);
          set({ board: null, toasts: [] });
          try { await postJSON("/api/replay/start", { date: day, speed }); } catch (e) { setErr(String((e as Error).message)); }
        }}>
        ▶ Start
      </button>
      {status?.mode === "REPLAY" && (
        <>
          <button className="px-2 py-0.5 rounded border hair" onClick={() => postJSON("/api/replay/control", { paused: !status.paused })}>
            {status.paused ? "Resume" : "Pause"}
          </button>
          <button className="px-2 py-0.5 rounded border hair text-mute" onClick={() => postJSON("/api/stop")}>Stop</button>
        </>
      )}
      {err && <span className="text-short">{err}</span>}
    </div>
  );
}

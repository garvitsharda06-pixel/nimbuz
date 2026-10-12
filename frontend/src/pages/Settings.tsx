import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getJSON, postJSON } from "../lib/api";
import { Panel } from "./Market";

interface SettingsResp {
  settings: Record<string, Record<string, unknown>>;
  config_hash: string;
  secrets: { upstox_token: boolean; telegram: boolean };
  provenance: string;
}

export function Settings() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => getJSON<SettingsResp>("/api/settings") });
  const [msg, setMsg] = useState<string | null>(null);
  if (!q.data) return <div className="p-4 text-mute">Loading…</div>;
  const s = q.data.settings;
  const sections = ["risk", "universe", "board", "session", "scoring", "backtest", "notify", "api", "replay"];
  return (
    <div className="p-3 grid grid-cols-1 lg:grid-cols-3 gap-3">
      <Panel title="Data & API status">
        <div className="text-[12px] space-y-1">
          <div>Provider: <span className="num">{q.data.provenance}</span> {q.data.provenance === "synthetic" && <span className="text-warn">(synthetic: plumbing/demo only)</span>}</div>
          <div>Upstox analytics token: {q.data.secrets.upstox_token ? <span className="text-long">configured</span> : <span className="text-warn">missing (.env)</span>}</div>
          <div>Telegram: {q.data.secrets.telegram ? <span className="text-long">configured (own chat only)</span> : <span className="text-mute">not configured</span>}</div>
          <div>Config hash: <span className="num">{q.data.config_hash}</span></div>
          <div className="pt-2 flex gap-2">
            <button className="px-2 py-1 rounded border border-long/50 text-long text-[12px]" onClick={async () => {
              try { await postJSON("/api/live/start"); setMsg("LIVE started"); } catch (e) { setMsg((e as Error).message); }
            }}>Start LIVE</button>
            <button className="px-2 py-1 rounded border hair text-mute text-[12px]" onClick={() => postJSON("/api/stop")}>Stop session</button>
          </div>
          {msg && <div className="text-warn text-[12px]">{msg}</div>}
          <div className="text-[11px] text-mute pt-2">
            Settings are edited in <span className="num">config/settings.yaml</span> (versioned, hashed into every journal row)
            and secrets in <span className="num">.env</span>. Restart the server to apply.
          </div>
        </div>
      </Panel>
      {sections.map((k) => (
        <Panel key={k} title={k}>
          <pre className="num text-[11px] whitespace-pre-wrap text-ink/90">{JSON.stringify(s[k], null, 1)}</pre>
        </Panel>
      ))}
    </div>
  );
}

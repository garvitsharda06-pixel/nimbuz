import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { getJSON, postJSON } from "../lib/api";
import { useTerminal } from "../store/terminal";

interface Note { day: string; kind: "morning" | "eod"; mode: string | null; text: string; created: string }

export function Notes() {
  const q = useQuery({ queryKey: ["notes"], queryFn: () => getJSON<{ notes: Note[] }>("/api/notes"), refetchInterval: 20000 });
  const status = useTerminal((s) => s.status);
  const [sel, setSel] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const notes = q.data?.notes ?? [];
  const n = notes[sel];
  const gen = async (kind: string) => {
    setBusy(kind);
    try {
      await postJSON(`/api/notes/generate?kind=${kind}&mode=${status?.mode === "LIVE" ? "LIVE" : "REPLAY"}`);
      await q.refetch();
      setSel(0);
    } finally { setBusy(null); }
  };
  return (
    <div className="p-3 grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-3">
      <div className="space-y-2">
        <div className="flex gap-2">
          <button className="px-2 py-1 rounded border border-accent/50 text-accent text-[12px]" onClick={() => gen("morning")} disabled={!!busy}>{busy === "morning" ? "…" : "Morning note"}</button>
          <button className="px-2 py-1 rounded border hair text-[12px]" onClick={() => gen("eod")} disabled={!!busy}>{busy === "eod" ? "…" : "EOD report"}</button>
        </div>
        <div className="text-[11px] text-mute">Auto: morning note 08:45 and EOD report 15:45 IST on trading days (LIVE). A replay writes a morning note at start and an EOD report at the end.</div>
        <ul className="rounded border hair divide-y divide-[var(--line)]">
          {notes.map((x, i) => (
            <li key={`${x.day}-${x.kind}-${x.mode}`}>
              <button onClick={() => setSel(i)} className={clsx("w-full text-left px-2 py-1.5 text-[12px]", sel === i ? "bg-panel2 text-ink" : "text-mute")}>
                <span className="num">{x.day}</span> · {x.kind === "morning" ? "Morning note" : "End-of-day"}{x.mode ? ` · ${x.mode}` : ""}
              </button>
            </li>
          ))}
          {!notes.length && <li className="px-2 py-2 text-mute text-[12px]">No notes yet.</li>}
        </ul>
      </div>
      <article className="rounded-md border hair bg-panel p-4 min-h-[300px]" data-testid="note">
        {n ? <pre className="num text-[12.5px] leading-relaxed whitespace-pre-wrap">{n.text}</pre> : <div className="text-mute">Select or generate a note.</div>}
        {n && <div className="text-[10.5px] text-mute pt-3">Written from computed numbers only (no LLM). Generated {n.created?.slice(0, 19).replace("T", " ")}.</div>}
      </article>
    </div>
  );
}

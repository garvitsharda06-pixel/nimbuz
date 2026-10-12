import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { hhmm, int, pctRaw, r, STATUS_LABEL } from "../lib/format";
import type { IdeaCard as Card } from "../lib/types";
import { useTerminal } from "../store/terminal";
import { IdeaCard } from "./IdeaCard";

function useFresh(ids: string[]) {
  const seen = useRef<Set<string>>(new Set());
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    const n = ids.filter((i) => !seen.current.has(i));
    if (n.length) {
      n.forEach((i) => seen.current.add(i));
      setFresh(new Set(n));
      const t = setTimeout(() => setFresh(new Set()), 1800);
      return () => clearTimeout(t);
    }
  }, [ids.join("|")]);  // eslint-disable-line react-hooks/exhaustive-deps
  return fresh;
}

function Column({ title, side, cards, selectedId, fresh, onOpen }: {
  title: string; side: "long" | "short"; cards: Card[]; selectedId: string | null; fresh: Set<string>; onOpen: (id: string) => void;
}) {
  return (
    <section className="min-w-0 flex flex-col" data-testid={`col-${side}`}>
      <div className="flex items-center justify-between px-1 pb-1.5">
        <h2 className={clsx("text-[12px] font-semibold tracking-wider", side === "long" ? "text-long" : "text-short")}>{title}</h2>
        <span className="num text-[11px] text-mute">{cards.length}/5</span>
      </div>
      <div className="space-y-2">
        {cards.map((c) => (
          <IdeaCard key={c.id} c={c} selected={selectedId === c.id} fresh={fresh.has(c.id)} onOpen={() => onOpen(c.id)} />
        ))}
        {cards.length === 0 && <EmptySide side={side} />}
      </div>
    </section>
  );
}

function EmptySide({ side }: { side: string }) {
  const board = useTerminal((s) => s.board);
  return (
    <div className="rounded-md border border-dashed hair px-3 py-6 text-center text-mute text-[12px]" data-testid={`empty-${side}`}>
      No {side} idea qualifies right now.
      {board && board.raw_signals_today > 0 && <div className="mt-1 text-[11px]">The board is never padded. See the reasons below.</div>}
    </div>
  );
}

export function Board() {
  const { board, search, selectedId, set } = useTerminal();
  const q = search.trim().toUpperCase();
  const filt = (cs: Card[]) => (q ? cs.filter((c) => c.symbol.includes(q) || c.sector.includes(q)) : cs);
  const longs = filt(board?.longs ?? []);
  const shorts = filt(board?.shorts ?? []);
  const fresh = useFresh([...longs, ...shorts].map((c) => c.id));
  const [mobileTab, setMobileTab] = useState<"long" | "short">("long");
  const touch = useRef<number | null>(null);
  const open = (id: string) => set({ selectedId: id, drawerId: id });
  const book = board?.book;
  const resolved = useMemo(() => filt(board?.resolved ?? []), [board, q]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="relative z-10 p-3 space-y-3">
      {book?.in_sample_day && board?.mode === "REPLAY" && (
        <div className="rounded border border-warn/40 bg-warn/10 text-warn px-3 py-1.5 text-[12px]" data-testid="in-sample">
          Replay day {board.day} is inside the stats book's sample ({book.data_range[0]} → {book.data_range[1]}), so the probabilities
          shown are in-sample for this day. For a clean out-of-sample replay, run <span className="num">dalal backtest --end &lt;day before&gt;</span>.
        </div>
      )}
      <GuardBar />
      <div className="md:hidden flex rounded border hair overflow-hidden text-[12px]">
        {(["long", "short"] as const).map((s) => (
          <button key={s} onClick={() => setMobileTab(s)}
            className={clsx("flex-1 py-1.5", mobileTab === s ? (s === "long" ? "bg-long/15 text-long" : "bg-short/15 text-short") : "text-mute")}>
            {s === "long" ? `LONGS (${longs.length})` : `SHORTS (${shorts.length})`}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3"
        onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
        onTouchEnd={(e) => {
          if (touch.current === null) return;
          const dx = e.changedTouches[0].clientX - touch.current;
          if (Math.abs(dx) > 60) setMobileTab(dx < 0 ? "short" : "long");
          touch.current = null;
        }}>
        <div className={clsx(mobileTab === "short" && "hidden md:block")}>
          <Column title="TOP LONGS" side="long" cards={longs} selectedId={selectedId} fresh={fresh} onOpen={open} />
        </div>
        <div className={clsx(mobileTab === "long" && "hidden md:block")}>
          <Column title="TOP SHORTS" side="short" cards={shorts} selectedId={selectedId} fresh={fresh} onOpen={open} />
        </div>
      </div>
      <Resolved cards={resolved} onOpen={open} />
      <div className="text-[11px] text-mute flex flex-wrap gap-x-4 gap-y-1 border-t hair pt-2">
        <span>Board as of <span className="num text-ink">{board?.as_of ?? "—"}</span></span>
        <span>Raw signals today <span className="num text-ink">{int(board?.raw_signals_today)}</span></span>
        <span>Pipeline <span className="num text-ink">{board?.engine_ms ?? "—"} ms</span></span>
        <span>Stats book: {book ? <span className="text-ink">{book.provenance} · {book.model_version} · qualified: {book.qualified.length ? book.qualified.join(", ") : "none"}</span> : <span className="text-warn">none (run `make backtest`)</span>}</span>
        {book?.demo_bypass && <span className="text-warn">Demo gate bypass ON (synthetic only)</span>}
      </div>
    </div>
  );
}

function GuardBar() {
  const board = useTerminal((s) => s.board);
  if (!board) return null;
  const rej = Object.entries(board.rejections ?? {});
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11.5px]" data-testid="guard">
      <span className={clsx("px-1.5 py-0.5 rounded border", board.guard.allowed ? "text-long border-long/40" : "text-warn border-warn/40")}>
        {board.guard.allowed ? "New ideas allowed" : "New ideas paused"}
      </span>
      {!board.guard.allowed && board.guard.reasons.map((x) => <span key={x} className="text-warn">{x}</span>)}
      {rej.length > 0 && <span className="text-mute">Filtered:</span>}
      {rej.slice(0, 5).map(([k, v]) => (
        <span key={k} className="text-mute"><span className="num text-ink">{v}</span> {k}</span>
      ))}
    </div>
  );
}

function Resolved({ cards, onOpen }: { cards: Card[]; onOpen: (id: string) => void }) {
  if (!cards.length) return null;
  return (
    <div>
      <div className="label pb-1">Resolved / expired today</div>
      <div className="rounded border hair overflow-x-auto scroll-thin">
        <table className="w-full text-[12px]">
          <tbody>
            {cards.map((c) => (
              <tr key={c.id} className="border-b hair last:border-0 hover:bg-panel2 cursor-pointer" onClick={() => onOpen(c.id)}>
                <td className="px-2 py-1 num text-mute">{hhmm(c.signal_ts)}</td>
                <td className={clsx("px-2 py-1 font-medium", c.side === "long" ? "text-long" : "text-short")}>{c.symbol}</td>
                <td className="px-2 py-1 text-mute">{c.setup_label}</td>
                <td className="px-2 py-1">{STATUS_LABEL[c.status] ?? c.status}</td>
                <td className="px-2 py-1 num text-right">{r(c.r_realised)}</td>
                <td className="px-2 py-1 num text-right text-mute">{pctRaw(c.p != null ? c.p * 100 : null, 0, false)}</td>
                {c.gate_bypassed && <td className="px-2 py-1 text-warn text-[10px]">demo</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

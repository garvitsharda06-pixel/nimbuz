import clsx from "clsx";
import { useEffect } from "react";
import { pct, px, STATUS_LABEL } from "../lib/format";
import { useTerminal } from "../store/terminal";

export function Toasts() {
  const { toasts, dropToast, set } = useTerminal();
  useEffect(() => {
    if (!toasts.length) return;
    const t = setTimeout(() => dropToast(toasts[toasts.length - 1].id), 8000);
    return () => clearTimeout(t);
  }, [toasts, dropToast]);
  return (
    <div className="fixed top-24 right-3 z-50 flex flex-col gap-2 w-[300px] max-w-[calc(100vw-24px)]" data-testid="toasts">
      {toasts.map((t) => {
        const long = t.card.side === "long";
        return (
          <button key={t.id} onClick={() => { set({ drawerId: t.card.id, selectedId: t.card.id }); dropToast(t.id); }}
            className={clsx("text-left rounded-md border bg-panel px-3 py-2 shadow-lg animate-slidein",
              long ? "border-long/60 animate-glowlong" : "border-short/60 animate-glowshort")}>
            <div className="flex items-baseline justify-between">
              <span className="font-semibold">{t.card.symbol} <span className={long ? "text-long" : "text-short"}>{long ? "LONG" : "SHORT"}</span></span>
              <span className="text-[10.5px] text-mute">{t.kind === "new" ? "NEW IDEA" : STATUS_LABEL[t.text ?? ""] ?? t.text}</span>
            </div>
            <div className="text-[11.5px] text-mute">{t.card.setup_label}</div>
            <div className="num text-[11.5px]">E {px(t.card.entry)} · S {px(t.card.stop)} · T1 {px(t.card.t1)} · p {pct(t.card.p)}</div>
            {t.card.gate_bypassed && <div className="text-[10px] text-warn mt-0.5">demo (gate bypassed, synthetic)</div>}
          </button>
        );
      })}
    </div>
  );
}

import { useTerminal } from "../store/terminal";
import { notify, soundLong, soundShort, soundUpdate } from "./alerts";
import type { BoardMsg, HealthMsg, IdeaCard, MarketMsg, StatusMsg } from "./types";

const dec = new TextDecoder();

export function connectWS(): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 500;

  const open = () => {
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      backoff = 500;
      useTerminal.getState().set({ connected: true });
    };
    ws.onclose = () => {
      useTerminal.getState().set({ connected: false });
      if (!closed) setTimeout(open, (backoff = Math.min(backoff * 2, 8000)));
    };
    ws.onmessage = (e) => {
      const m = JSON.parse(typeof e.data === "string" ? e.data : dec.decode(e.data));
      handle(m);
    };
  };
  open();
  return () => {
    closed = true;
    ws?.close();
  };
}

function handle(m: { type: string } & Record<string, unknown>) {
  const st = useTerminal.getState();
  switch (m.type) {
    case "status":
      st.set({ status: m as unknown as StatusMsg, clock: (m as unknown as StatusMsg).clock });
      break;
    case "health":
      st.set({ health: m as unknown as HealthMsg });
      break;
    case "board":
      st.set({ board: m as unknown as BoardMsg });
      break;
    case "market":
      st.set({ market: m as unknown as MarketMsg });
      break;
    case "prices":
      st.applyPrices(m.data as Record<string, number>);
      break;
    case "minute":
      st.set({ clock: m.close_ts as string });
      st.applyPrices(m.prices as Record<string, number>);
      break;
    case "idea_new": {
      const card = m.card as IdeaCard;
      st.pushToast({ id: `${card.id}-new`, card, at: Date.now(), kind: "new" });
      if (!st.muted) (card.side === "long" ? soundLong : soundShort)();
      notify(`${card.side === "long" ? "LONG" : "SHORT"} ${card.symbol} · ${card.setup_label}`,
        `Entry ${card.entry} · Stop ${card.stop} · T1 ${card.t1} · p ${card.p != null ? Math.round(card.p * 100) + "%" : "—"}`);
      break;
    }
    case "idea_update": {
      const card = m.card as IdeaCard;
      const important = ["TRIGGERED", "T1_HIT", "T2_HIT", "STOPPED"].includes(m.new as string);
      if (important) {
        st.pushToast({ id: `${card.id}-${m.new}`, card, at: Date.now(), kind: "update", text: m.new as string });
        if (!st.muted) soundUpdate();
        notify(`${card.symbol} ${String(m.new).replace("_", " ")}`, `${card.setup_label} · ${card.side}`);
      }
      break;
    }
    case "error":
      st.set({ lastError: m.message as string });
      break;
  }
}

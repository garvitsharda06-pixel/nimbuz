import { useEffect, useRef, useState } from "react";
import { Board } from "./components/Board";
import { Drawer } from "./components/Drawer";
import { Toasts } from "./components/Toasts";
import { TopBar } from "./components/TopBar";
import { connectWS } from "./lib/ws";
import { Lab } from "./pages/Lab";
import { Market } from "./pages/Market";
import { Notes } from "./pages/Notes";
import { Performance } from "./pages/Performance";
import { Settings } from "./pages/Settings";
import { allBoardCards, useTerminal } from "./store/terminal";

export default function App() {
  const [tab, setTab] = useState("Board");
  const status = useTerminal((s) => s.status);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => connectWS(), []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const st = useTerminal.getState();
      const typing = (e.target as HTMLElement)?.tagName === "INPUT" || (e.target as HTMLElement)?.tagName === "SELECT";
      if (e.key === "Escape") {
        if (st.drawerId) st.set({ drawerId: null });
        else if (typing) (e.target as HTMLElement).blur();
        return;
      }
      if (typing) return;
      const cards = allBoardCards(st.board);
      const idx = cards.findIndex((c) => c.id === st.selectedId);
      if (e.key === "j" || e.key === "J") {
        const n = cards[Math.min(cards.length - 1, idx + 1)];
        if (n) st.set({ selectedId: n.id });
      } else if (e.key === "k" || e.key === "K") {
        const n = cards[Math.max(0, idx - 1)];
        if (n) st.set({ selectedId: n.id });
      } else if (e.key === "Enter" && st.selectedId) {
        st.set({ drawerId: st.selectedId });
      } else if (e.key === "/") {
        e.preventDefault();
        setTab("Board");
        setTimeout(() => search.current?.focus(), 0);
      } else if (e.key === " ") {
        e.preventDefault();
        st.set({ muted: !st.muted });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="min-h-full flex flex-col">
      {status?.synthetic && <div className="watermark">SYNTHETIC DATA · NOT MARKET DATA</div>}
      <TopBar tab={tab} setTab={setTab} />
      {tab === "Board" && (
        <div className="px-3 pt-2 relative z-10">
          <input ref={search} placeholder="Search symbol / sector  ( / )" data-testid="search"
            className="w-full md:w-72 bg-panel2 border hair rounded px-2 py-1 text-[12px]"
            onChange={(e) => useTerminal.getState().set({ search: e.target.value })} />
        </div>
      )}
      <main className="flex-1 relative z-10">
        {tab === "Board" && <Board />}
        {tab === "Market" && <Market />}
        {tab === "Performance" && <Performance />}
        {tab === "Lab" && <Lab />}
        {tab === "Notes" && <Notes />}
        {tab === "Settings" && <Settings />}
      </main>
      <footer className="relative z-10 px-3 py-2 text-[10.5px] text-mute border-t hair">
        Personal research terminal. Places no orders. Signals are for the owner only (SEBI RA rules). Probabilities are measured
        out-of-sample, not promises. Charts by TradingView Lightweight Charts™.
      </footer>
      <Toasts />
      <Drawer />
    </div>
  );
}

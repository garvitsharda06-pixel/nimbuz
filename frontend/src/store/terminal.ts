import { create } from "zustand";
import type { BoardMsg, HealthMsg, IdeaCard, MarketMsg, StatusMsg } from "../lib/types";

export interface Toast { id: string; card: IdeaCard; at: number; kind: "new" | "update"; text?: string }

interface TerminalState {
  connected: boolean;
  status: StatusMsg | null;
  health: HealthMsg | null;
  board: BoardMsg | null;
  market: MarketMsg | null;
  prices: Record<string, number>;
  prevPrices: Record<string, number>;
  clock: string | null;
  toasts: Toast[];
  selectedId: string | null;
  drawerId: string | null;
  muted: boolean;
  search: string;
  lastError: string | null;
  set: (p: Partial<TerminalState>) => void;
  pushToast: (t: Toast) => void;
  dropToast: (id: string) => void;
  applyPrices: (d: Record<string, number>) => void;
}

export const useTerminal = create<TerminalState>((set, get) => ({
  connected: false,
  status: null,
  health: null,
  board: null,
  market: null,
  prices: {},
  prevPrices: {},
  clock: null,
  toasts: [],
  selectedId: null,
  drawerId: null,
  muted: false,
  search: "",
  lastError: null,
  set: (p) => set(p),
  pushToast: (t) => set({ toasts: [t, ...get().toasts].slice(0, 3) }),
  dropToast: (id) => set({ toasts: get().toasts.filter((x) => x.id !== id) }),
  applyPrices: (d) => {
    const cur = get().prices;
    const prev: Record<string, number> = {};
    for (const k of Object.keys(d)) if (k in cur) prev[k] = cur[k];
    set({ prices: { ...cur, ...d }, prevPrices: { ...get().prevPrices, ...prev } });
  },
}));

export const allBoardCards = (b: BoardMsg | null): IdeaCard[] => (b ? [...b.longs, ...b.shorts] : []);

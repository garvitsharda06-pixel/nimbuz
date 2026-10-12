export type Side = "long" | "short";
export type Status =
  | "WATCHING" | "TRIGGERED" | "T1_HIT" | "T2_HIT" | "STOPPED" | "EXPIRED" | "INVALIDATED" | "CLOSED" | "DISPLACED";

export interface IdeaCard {
  id: string;
  day: string;
  signal_ts: string;
  symbol: string;
  key: string;
  sector: string;
  setup: string;
  setup_label: string;
  side: Side;
  regime: string;
  grade: "A+" | "A" | "B";
  entry: number;
  entry_zone: [number, number];
  stop: number;
  t1: number;
  t2: number;
  rr1: number;
  rr2: number;
  p: number | null;
  ci_lo: number | null;
  ci_hi: number | null;
  n: number;
  expectancy: number | null;
  cons_ev: number | null;
  breakeven: number | null;
  win_r: number | null;
  loss_r: number | null;
  qty: number;
  risk_inr: number;
  notional: number;
  valid_until: string;
  expires_at: string;
  thesis: string[];
  invalidation: string;
  beta: number | null;
  model_version: string;
  config_hash: string;
  provenance: string;
  mode: string;
  status: Status;
  status_ts: string;
  entry_px: number | null;
  exit_px: number | null;
  r_realised: number | null;
  last_price: number | null;
  gate_bypassed: boolean;
  gate_reasons: string[];
  live_hit: number | null;
  live_n: number;
  stats_level: string;
  snapshot: Record<string, number | string | null>;
  distance_to_entry_pct: number | null;
  spark?: { c: (number | null)[]; vwap: (number | null)[] } | null;
  events?: { ts: string; status: string; price: number | null; note: string }[];
  paper?: boolean;
}

export interface BoardMsg {
  type: "board";
  day: string;
  mode: string;
  as_of: string | null;
  longs: IdeaCard[];
  shorts: IdeaCard[];
  resolved: IdeaCard[];
  guard: { allowed: boolean; reasons: string[]; pnl_today?: number };
  rejections: Record<string, number>;
  raw_signals_today: number;
  book: null | { provenance: string; created: string; model_version: string; qualified: string[]; demo_bypass: boolean;
    data_range: [string, string]; in_sample_day: boolean };
  engine_ms: number | null;
  provenance: string;
}

export interface Quote { ltp: number | null; chg_pct: number | null }

export interface MarketMsg {
  type: "market";
  as_of?: string;
  regime?: string;
  regime_score?: number | null;
  breadth_pct?: number | null;
  adv_pct?: number | null;
  breadth_series?: (number | null)[];
  nifty?: Quote | null;
  banknifty?: Quote | null;
  vix?: Quote | null;
  usdinr?: Quote | null;
  sector_rs_series?: Record<string, (number | null)[]>;
  oi_buildup?: { symbol: string; sector: string; state: string; oi_chg_pct: number; fut_ret_pct: number }[] | null;
  sectors?: { name: string; ret_pct: number | null; rank: number | null; ret_open_pct: number | null }[];
  volume_shockers?: { symbol: string; sector: string; rvol: number; chg_pct: number; ltp: number; rs_z: number | null }[];
  rs_leaders?: { symbol: string; sector: string; rvol: number; chg_pct: number; ltp: number; rs_z: number | null }[];
  rs_laggards?: { symbol: string; sector: string; rvol: number; chg_pct: number; ltp: number; rs_z: number | null }[];
  universe?: number;
  excluded?: number;
}

export interface StatusMsg {
  type: "status";
  mode: "IDLE" | "LIVE" | "REPLAY";
  provenance: string;
  synthetic: boolean;
  replay_day: string | null;
  speed: number | null;
  paused: boolean | null;
  clock: string;
  market_status: string;
  kill_switch: boolean;
  n_instruments: number;
  config_hash: string;
}

export interface HealthMsg {
  type: "health";
  state: string;
  signals_allowed: boolean;
  last_event_age_s: number | null;
  latency_ms: number | null;
  clock_drift_ms: number | null;
  missing_bars: number;
  reconnects: number;
  reasons: string[];
}

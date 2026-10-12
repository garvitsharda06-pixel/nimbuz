const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const dash = "—";
export const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

export const px = (x: number | null | undefined) => (isNum(x) ? inr2.format(x) : dash);
export const rupees = (x: number | null | undefined) => (isNum(x) ? `₹${inr.format(x)}` : dash);
export const int = (x: number | null | undefined) => (isNum(x) ? inr.format(x) : dash);
export const pct = (x: number | null | undefined, digits = 0) => (isNum(x) ? `${(x * 100).toFixed(digits)}%` : dash);
export const pctRaw = (x: number | null | undefined, digits = 2, sign = true) =>
  isNum(x) ? `${sign && x > 0 ? "+" : ""}${x.toFixed(digits)}%` : dash;
export const r = (x: number | null | undefined, digits = 2) => (isNum(x) ? `${x > 0 ? "+" : ""}${x.toFixed(digits)}R` : dash);
export const hhmm = (iso?: string | null) => (iso ? iso.slice(11, 16) : dash);
export const hhmmss = (iso?: string | null) => (iso ? iso.slice(11, 19) : dash);

export const STATUS_LABEL: Record<string, string> = {
  WATCHING: "Watching", TRIGGERED: "Triggered", T1_HIT: "T1 hit", T2_HIT: "T2 hit", STOPPED: "Stopped",
  EXPIRED: "Expired", INVALIDATED: "Invalidated", CLOSED: "Closed", DISPLACED: "Displaced",
};

export const statusTone = (s: string) =>
  s === "TRIGGERED" ? "text-accent border-accent/40"
    : s === "T1_HIT" || s === "T2_HIT" ? "text-long border-long/40"
    : s === "STOPPED" ? "text-short border-short/40"
    : s === "WATCHING" ? "text-ink border-line"
    : "text-mute border-line";

export const regimeLabel = (r?: string | null) => (r ? r.replace(/_/g, " ") : dash);
export const regimeTone = (r?: string | null) =>
  r === "TREND_UP" ? "text-long border-long/40 bg-long/5"
    : r === "TREND_DOWN" ? "text-short border-short/40 bg-short/5"
    : r === "HIGH_VOL_CHOP" ? "text-warn border-warn/40 bg-warn/5"
    : "text-ink border-line";

// Alert sounds (WebAudio, no assets) and browser notifications. Personal use only: nothing is broadcast.
let ctx: AudioContext | null = null;

function tone(freqs: number[], dur = 0.11) {
  try {
    ctx = ctx ?? new AudioContext();
    let t = ctx.currentTime;
    for (const f of freqs) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(0.12, t + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + dur + 0.02);
      t += dur * 0.9;
    }
  } catch {
    /* audio unavailable */
  }
}

export const soundLong = () => tone([660, 880]);      // rising: long
export const soundShort = () => tone([660, 494]);     // falling: short
export const soundUpdate = () => tone([740], 0.07);

export async function enableNotifications(): Promise<boolean> {
  if (!("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  return (await Notification.requestPermission()) === "granted";
}

export function notify(title: string, body: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") return;
  if (!document.hidden) return;                          // only when the tab is hidden
  try {
    new Notification(title, { body, tag: title });
  } catch {
    /* ignore */
  }
}

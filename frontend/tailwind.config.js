/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        panel: "var(--panel)",
        panel2: "var(--panel2)",
        line: "var(--line)",
        ink: "var(--ink)",
        mute: "var(--mute)",
        long: "var(--long)",
        short: "var(--short)",
        warn: "var(--warn)",
        accent: "var(--accent)",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["'JetBrains Mono'", "ui-monospace", "monospace"],
      },
      keyframes: {
        slidein: { "0%": { transform: "translateX(24px)", opacity: "0" }, "100%": { transform: "none", opacity: "1" } },
        glowlong: { "0%": { boxShadow: "0 0 0 1px var(--long), 0 0 18px -2px var(--long)" }, "100%": { boxShadow: "none" } },
        glowshort: { "0%": { boxShadow: "0 0 0 1px var(--short), 0 0 18px -2px var(--short)" }, "100%": { boxShadow: "none" } },
        flashup: { "0%": { backgroundColor: "rgba(34,197,94,.30)" }, "100%": { backgroundColor: "transparent" } },
        flashdn: { "0%": { backgroundColor: "rgba(239,68,68,.30)" }, "100%": { backgroundColor: "transparent" } },
      },
      animation: {
        slidein: "slidein .28s ease-out",
        glowlong: "glowlong 1.6s ease-out",
        glowshort: "glowshort 1.6s ease-out",
        flashup: "flashup .7s ease-out",
        flashdn: "flashdn .7s ease-out",
      },
    },
  },
  plugins: [],
};

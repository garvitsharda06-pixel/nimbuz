import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";

const exe = process.env.PW_CHROMIUM ?? (existsSync("/opt/pw-browsers/chromium") ? "/opt/pw-browsers/chromium" : undefined);

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:8000",
    launchOptions: exe ? { executablePath: exe } : {},
    trace: "retain-on-failure",
  },
  webServer: {
    // serves the built UI (frontend/dist) + API; --demo bypasses the gate ONLY on synthetic data
    command: "cd ../backend && .venv/bin/dalal serve --demo",
    url: "http://127.0.0.1:8000/api/status",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});

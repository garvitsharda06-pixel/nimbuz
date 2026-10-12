import { expect, test } from "@playwright/test";

test("live board on a replayed day: ideas appear, update and the terminal behaves", async ({ page, request }) => {
  const days = (await (await request.get("/api/days")).json()).days as string[];
  expect(days.length).toBeGreaterThan(0);
  await page.goto("/");
  await expect(page.getByTestId("mode")).toBeVisible();
  const r = await request.post("/api/replay/start", { data: { date: days[days.length - 2], speed: 60, start_time: "09:30" } });
  expect(r.ok()).toBeTruthy();
  await expect(page.getByTestId("mode")).toContainText("REPLAY", { timeout: 15_000 });
  // synthetic data must be labelled everywhere
  await expect(page.getByText("SYNTHETIC", { exact: true })).toBeVisible();
  // an idea card appears within the replay (demo bypass on synthetic data)
  const card = page.getByTestId("idea-card").first();
  await expect(card).toBeVisible({ timeout: 90_000 });
  await expect(card).toContainText("GATE BYPASSED");
  await expect(card.getByTestId("prob")).toContainText("%");
  // keyboard: J selects, Enter opens the drawer with chart + evidence, Esc closes
  await page.keyboard.press("j");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("drawer")).toBeVisible();
  await expect(page.getByTestId("chart")).toBeVisible();
  await expect(page.getByTestId("stats")).toContainText("Calibrated P(T1 first)");
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("drawer")).toBeHidden();
  // market overview renders the sector heatmap and rotation chart
  await page.getByRole("button", { name: "Market", exact: true }).click();
  await expect(page.getByTestId("treemap")).toBeVisible();
  await expect(page.getByTestId("rrg")).toBeVisible();
  await page.getByRole("button", { name: "Board", exact: true }).click();
  // kill switch pauses new ideas
  await page.getByTestId("kill").click();
  await expect(page.getByText("KILL SWITCH ON")).toBeVisible();
  await page.getByTestId("kill").click();
  await request.post("/api/stop");
});

test("mobile: longs and shorts become tabs", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await page.goto("/");
  await expect(page.getByRole("button", { name: /LONGS/ })).toBeVisible();
  await page.getByRole("button", { name: /SHORTS/ }).click();
  await expect(page.getByTestId("col-short")).toBeVisible();
  await ctx.close();
});

test("track record: a fully replayed day is journaled, resolved and reported", async ({ page, request }) => {
  const days = (await (await request.get("/api/days")).json()).days as string[];
  const day = days[days.length - 3];
  await request.post("/api/replay/start", { data: { date: day, speed: 0 } });
  await expect.poll(async () => (await (await request.get("/api/status")).json()).mode, { timeout: 90_000 }).toBe("IDLE");
  const perf = await (await request.get("/api/performance?mode=REPLAY&include_demo=true")).json();
  expect(perf.n_shown).toBeGreaterThan(0);
  await page.goto("/");
  await page.getByRole("button", { name: "Performance", exact: true }).click();
  await page.getByRole("button", { name: "REPLAY", exact: true }).click();
  await page.getByLabel("include demo").check();
  await expect(page.getByTestId("reliability")).toBeVisible();
  await page.getByRole("button", { name: "Notes", exact: true }).click();
  await page.getByText(`${day} · End-of-day · REPLAY`).click();
  await expect(page.getByTestId("note")).toContainText("What fired");
});

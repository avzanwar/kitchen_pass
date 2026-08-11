/**
 * End-to-end tests against a real browser, a real API and a real database.
 *
 * The offline test is the one that matters most: it is the only place the
 * whole Phase 7 story — local fold, IndexedDB queue, reconnect, converge — is
 * exercised the way a scorekeeper on bad court wifi would exercise it.
 *
 * Assumes the stack is already running:
 *   server: KP_DEBUG=true uv run uvicorn app.main:app --port 8000
 *   web:    npm run dev
 * and that scripts/seed.py has been run.
 */

import { expect, test, type Page } from "@playwright/test";

const EMAIL = "organizer@kitchenpass.dev";
const PASSWORD = "seed-password-123";

async function signIn(page: Page): Promise<void> {
  await page.goto("/signin");
  await page.getByPlaceholder("Email").fill(EMAIL);
  await page.getByPlaceholder("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Kitchen Pass Spring Open")).toBeVisible();
}

/** Open a division that has playable matches and start scoring one. */
async function openLiveMatch(page: Page, division: string): Promise<void> {
  await page.getByText("Kitchen Pass Spring Open").click();
  await page.getByText(division).click();
  await page.getByRole("button", { name: "Draw" }).click();
  await page.getByRole("button", { name: /Score/ }).first().click();
  await expect(page.getByText("Point", { exact: true })).toBeVisible();
}

test("signs in and lists the seeded tournament", async ({ page }) => {
  await signIn(page);
  await expect(page.getByText("registration")).toBeVisible();
});

test("shows divisions, courts and the court board", async ({ page }) => {
  await signIn(page);
  await page.getByText("Kitchen Pass Spring Open").click();

  await expect(page.getByText("4.0 Mixed Doubles")).toBeVisible();
  await expect(page.getByText("Open Singles")).toBeVisible();

  await page.getByRole("button", { name: "Courts" }).click();
  await expect(page.getByText("Court 1")).toBeVisible();

  await page.getByRole("button", { name: "Court board" }).click();
  await expect(page.getByRole("button", { name: /Auto-assign/ })).toBeVisible();
});

test("shows a generated draw with pool labels", async ({ page }) => {
  await signIn(page);
  await page.getByText("Kitchen Pass Spring Open").click();
  await page.getByText("4.0 Mixed Doubles").click();
  await page.getByRole("button", { name: "Draw" }).click();

  // Pool matches are ready; the playoff slots are still unresolved references.
  await expect(page.getByText(/Pool A/).first()).toBeVisible();
  await expect(page.getByText(/A1/).first()).toBeVisible();
});

test("scores a rally and the call updates", async ({ page }) => {
  await signIn(page);
  await openLiveMatch(page, "3.5 Men's Doubles");

  await expect(page.getByText("0-0-2")).toBeVisible();
  await page.getByText("Point", { exact: true }).click();

  // Serving team scored: score 1, and the server has moved to the left.
  await expect(page.getByText("1-0-2")).toBeVisible();
  await expect(page.getByText(/from the left/)).toBeVisible();
});

test("undo reverses a rally", async ({ page }) => {
  await signIn(page);
  await openLiveMatch(page, "3.5 Men's Doubles");

  await page.getByText("Point", { exact: true }).click();
  await page.getByText("Point", { exact: true }).click();
  await expect(page.getByText("2-0-2")).toBeVisible();

  await page.getByRole("button", { name: /Undo last/ }).click();
  await expect(page.getByText("1-0-2")).toBeVisible();
});

test("a side out hands serve to the other team", async ({ page }) => {
  await signIn(page);
  await openLiveMatch(page, "3.5 Men's Doubles");

  // The game's first server is number 2, so one fault is an immediate side out.
  await page.getByText("Side out", { exact: true }).click();
  await expect(page.getByText("0-0-1")).toBeVisible();
});

test("scores offline and converges after reconnecting", async ({ page, context }) => {
  await signIn(page);
  await openLiveMatch(page, "3.5 Men's Doubles");

  await page.getByText("Point", { exact: true }).click();
  await expect(page.getByText("1-0-2")).toBeVisible();

  // Pull the network out mid-match.
  await context.setOffline(true);
  await page.getByText("Point", { exact: true }).click();
  await page.getByText("Point", { exact: true }).click();
  await page.getByText("Point", { exact: true }).click();

  // The board keeps working, and the queued rallies are visible as pending.
  await expect(page.getByText("4-0-2")).toBeVisible();
  await expect(page.getByText(/Offline/)).toBeVisible();
  await expect(page.getByText(/3 rallies queued/)).toBeVisible();

  // Reconnect: the outbox flushes and the banner clears.
  await context.setOffline(false);
  await expect(page.getByText(/queued/)).toBeHidden({ timeout: 20_000 });

  // A hard reload proves the score reached the server, not just the tab.
  await page.reload();
  await expect(page.getByText("4-0-2")).toBeVisible();
});

test("offline rallies survive a reload before syncing", async ({ page, context }) => {
  await signIn(page);
  await openLiveMatch(page, "Open Singles");

  await context.setOffline(true);
  await page.getByText("Point", { exact: true }).click();
  await page.getByText("Point", { exact: true }).click();
  await expect(page.getByText(/2 rallies queued/)).toBeVisible();

  // IndexedDB, not memory: the queue must outlive the page.
  await page.reload();
  await expect(page.getByText(/queued/)).toBeVisible({ timeout: 15_000 });

  await context.setOffline(false);
  await expect(page.getByText(/queued/)).toBeHidden({ timeout: 20_000 });
});

test("public view needs no account and shows standings", async ({ page, request }) => {
  const token = await request
    .post("/api/v1/auth/login", { data: { email: EMAIL, password: PASSWORD } })
    .then((r) => r.json())
    .then((body: { access_token: string }) => body.access_token);
  const tournaments = await request
    .get("/api/v1/tournaments", { headers: { Authorization: `Bearer ${token}` } })
    .then((r) => r.json());
  const publicToken = tournaments[0].public_token as string;

  // A fresh context with no stored credentials.
  await page.context().clearCookies();
  await page.goto(`/live/${publicToken}`);

  await expect(page.getByRole("heading", { name: "Kitchen Pass Spring Open" }))
    .toBeVisible();
  await expect(page.getByText("On court")).toBeVisible();

  await page.getByRole("button", { name: "3.5 Men's Doubles" }).click();
  await expect(page.getByText("Standings")).toBeVisible();
  await expect(page.getByRole("link", { name: "Standings CSV" })).toBeVisible();
});

test("registers a player and it appears in the roster", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "Players" }).click();
  await page.getByRole("button", { name: /Add player/ }).click();

  const name = `E2E Tester ${Date.now()}`;
  await page.getByPlaceholder("Name").fill(name);
  await page.getByRole("button", { name: "Save" }).click();

  await expect(page.getByText(name)).toBeVisible();
});

import { defineConfig, devices } from "@playwright/test";

/**
 * Runs against the dev stack. Start the API and Vite first:
 *   cd server && KP_DEBUG=true uv run uvicorn app.main:app --port 8000
 *   cd web    && npm run dev
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    // A phone, because that is what a scorekeeper is holding.
    ...devices["iPhone 13"],
  },
});

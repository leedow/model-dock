import { defineConfig } from "@playwright/test";
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.no_proxy = process.env.NO_PROXY;
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120000,
  use: {
    baseURL: "http://127.0.0.1:8010",
    channel: "chromium",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    actionTimeout: 10000,
  },
  webServer: {
    command: "../.venv/bin/python ../tests/browser_server.py",
    url: "http://127.0.0.1:8010/health/live",
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
  reporter: "list",
});

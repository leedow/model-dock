import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// An isolated harness prevents other chats' browser suites from changing our sessions.
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false,
  timeout: 120000,
  reporter: "list",
  testMatch: ["remote.spec.ts", "console.spec.ts", "connections.spec.ts"],
  outputDir: "/tmp/modeldock-provider-browser-results",
  use: { ...base.use, baseURL: "http://127.0.0.1:8012" },
  webServer: {
    command:
      "MODELDock_BROWSER_PORT=8012 ../.venv/bin/python ../tests/browser_server.py",
    url: "http://127.0.0.1:8012/health/live",
    reuseExistingServer: false,
    timeout: 30000,
  },
});

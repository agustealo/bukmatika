import { defineConfig } from "@playwright/test";

const ci = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: ci ? 1 : undefined,
  retries: ci ? 1 : 0,
  reporter: ci ? "line" : "list",
  timeout: 30_000,
  expect: {
    timeout: 8_000,
  },
  use: {
    baseURL: "http://127.0.0.1:3000",
    browserName: "chromium",
    headless: true,
    trace: ci ? "retain-on-failure" : "on-first-retry",
  },
  webServer: [
    {
      command: "python -m uvicorn bukmatika.main:app --host 127.0.0.1 --port 8000",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: !ci,
      timeout: 120_000,
    },
    {
      command:
        "npm run start --workspace @bukmatika/web -- --hostname 127.0.0.1 --port 3000",
      url: "http://127.0.0.1:3000/research",
      reuseExistingServer: !ci,
      timeout: 120_000,
    },
  ],
});

import { defineConfig } from "@playwright/test";

/**
 * End-to-end: real API + worker + web app against a freshly seeded test database.
 * Run with `make e2e` (it resets and seeds the database first).
 */
export const backendEnv: Record<string, string> = {
  DATABASE_URL: process.env.E2E_DATABASE_URL ?? "postgresql://returns:returns@localhost:5432/returns_test",
  JWT_SECRET: process.env.JWT_SECRET ?? "e2e-only-secret-xxxxxxxxxxxxxxxxxxxxxxxxxx",
  PII_ENCRYPTION_KEY: process.env.PII_ENCRYPTION_KEY ?? "ZTJlLW9ubHkta2V5LWZvci1sb2NhbC10ZXN0cy0wMDA=",
  PII_INDEX_KEY: process.env.PII_INDEX_KEY ?? "e2e-index-key",
  WEBHOOK_SECRET: "e2e-webhook-secret",
  LLM_PROVIDER: process.env.E2E_LLM_PROVIDER ?? "none",
  DEV_OTP_ECHO: "true",
  EVIDENCE_DIR: "var/e2e-evidence",
};

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  workers: 1,
  fullyParallel: false,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
    launchOptions: process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  },
  webServer: [
    {
      command: "uv run --directory ../../services uvicorn returns_agent.api.main:app --port 8000",
      url: "http://localhost:8000/healthz",
      env: backendEnv,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npx next start -p 3000",
      url: "http://localhost:3000",
      env: { API_URL: "http://localhost:8000" },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});

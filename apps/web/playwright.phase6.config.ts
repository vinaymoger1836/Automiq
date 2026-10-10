import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "phase6.spec.ts",
  outputDir: "../../artifacts/e2e/phase6-browser/results",
  reporter: [["list"], ["junit", { outputFile: "../../artifacts/e2e/phase6-browser/playwright.xml" }]],
  use: { baseURL: process.env.WEB_ORIGIN || "http://localhost:3000", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

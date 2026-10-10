import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "phase4.spec.ts",
  outputDir: "../../artifacts/e2e/phase4-browser/results",
  reporter: [["list"], ["junit", { outputFile: "../../artifacts/e2e/phase4-browser/playwright.xml" }]],
  use: { baseURL: process.env.WEB_ORIGIN || "http://localhost:3000", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

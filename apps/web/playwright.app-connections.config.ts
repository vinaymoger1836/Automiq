import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "app-connections.spec.ts",
  outputDir: "../../artifacts/e2e/app-connections/browser-results",
  reporter: [["list"], ["junit", { outputFile: "../../artifacts/e2e/app-connections/playwright.xml" }]],
  use: { baseURL: process.env.WEB_ORIGIN || "http://localhost:3000", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

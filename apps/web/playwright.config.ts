import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  reporter: [["junit", { outputFile: "../../artifacts/e2e/phase0/playwright.xml" }]],
  use: { baseURL: process.env.WEB_ORIGIN || "http://localhost:3000", trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});

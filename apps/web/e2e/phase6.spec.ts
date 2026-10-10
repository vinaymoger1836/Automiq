import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/phase6-browser");

test("starter template publishes and runs across themes and widths", async ({ page }) => {
  test.setTimeout(120000);
  mkdirSync(evidenceDir, { recursive: true });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const suffix = Math.random().toString(36).slice(2, 10);
  await page.getByLabel("New workspace").fill(`Phase 6 browser ${suffix}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Create your first workflow" })).toBeVisible();
  await page.getByLabel("Create a workflow").fill("Starter example");
  await page.getByLabel("Starting point").selectOption("starter");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(page.getByText("Starter draft is ready to publish or customize.")).toBeVisible();
  await expect(page.getByLabel("Workspace overview").locator("strong")).toHaveText(["1", "0", "0"]);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: /Publish/ }).click();
  await page.getByRole("button", { name: "Confirm publish" }).click();
  await expect(page.getByText("Version 1 published.")).toBeVisible();
  await expect(page.getByLabel("Workspace overview").locator("strong").nth(1)).toHaveText("1");
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 45000 });
  await expect(page.locator(".run-steps article")).toHaveCount(3);

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      localStorage.setItem("automiq-theme", value);
    }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "Starter example" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.screenshot({ path: resolve(evidenceDir, `starter-${theme}-${width}.png`), fullPage: true });
      }
    }
  }
});

import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

test("health dashboard works in both themes and viewport sizes", async ({ page }) => {
  const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/phase0");
  mkdirSync(evidenceDir, { recursive: true });
  for (const theme of ["light", "dark"] as const) {
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => localStorage.setItem("automiq-theme", value), theme);
      await page.goto("/");
      await expect(page.getByRole("heading", { name: "System status" })).toBeVisible();
      await expect(page.getByText("All systems ready")).toBeVisible();
      await expect(page.getByText("PostgreSQL")).toBeVisible();
      await expect(page.getByText("Temporal")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.screenshot({ path: resolve(evidenceDir, `${theme}-${width}.png`), fullPage: true });
      }
    }
  }
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const revisit = await page.context().newPage();
  await revisit.goto("/");
  await expect(revisit.locator("html")).toHaveAttribute("data-theme", "light");
  await revisit.close();
});

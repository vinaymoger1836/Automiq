import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/app-connections/browser");

test("local owner can sign in and create a workspace from both loopback URLs", async ({ page }) => {
  for (const origin of ["http://localhost:3000", "http://127.0.0.1:3000"]) {
    await page.goto(`${origin}/studio`);
    await page.getByRole("button", { name: "Sign in as owner" }).click();
    await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
    const name = `Loopback ${randomUUID().slice(0, 8)}`;
    await page.getByLabel("New workspace").fill(name);
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page.getByLabel("Current workspace")).toContainText(name);
  }
});

test("owner registers a custom app without a provider-specific connector", async ({ page }) => {
  mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const nonce = randomUUID().slice(0, 8);
  await page.getByLabel("New workspace").fill(`App UI ${nonce}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByLabel("Create a workflow").fill("Custom app flow");
  await page.getByRole("button", { name: "Create workflow" }).click();
  const panel = page.locator(".integration-card").filter({ has: page.getByRole("heading", { name: "Custom apps" }) });
  await expect(panel).toBeVisible();
  await panel.getByLabel("Name", { exact: true }).fill(`Synthetic app ${nonce}`);
  await panel.getByRole("button", { name: "Create connection" }).click();
  await expect(panel.getByText(`Synthetic app ${nonce}`).first()).toBeVisible();
  await expect(panel.getByLabel("Inbound signing secret")).toHaveValue(/.+/);
  await panel.getByRole("button", { name: "Done copying" }).click();
  await expect(panel.getByLabel("Inbound signing secret")).toHaveCount(0);

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      localStorage.setItem("automiq-theme", value);
    }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(panel.getByRole("heading", { name: "Custom apps" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await panel.screenshot({ path: resolve(evidenceDir, `${theme}-${width}-custom-app.png`) });
      }
    }
  }
});

test("owner configures durable wait nodes in both themes and viewport sizes", async ({ page }) => {
  mkdirSync(evidenceDir, { recursive: true });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const nonce = randomUUID().slice(0, 8);
  await page.getByLabel("New workspace").fill(`Wait UI ${nonce}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByLabel("Create a workflow").fill("Wait for completion");
  await page.getByRole("button", { name: "Create workflow" }).click();
  const palette = page.getByLabel("Node palette");
  await palette.getByRole("button", { name: /Wait for app event/ }).click();
  await expect(page.getByLabel("Required correlation field")).toHaveValue("task_id");
  await expect(page.getByLabel("Value from starting event")).toHaveValue("trigger.payload.task_id");
  await palette.getByRole("button", { name: /Wait for PR merge/ }).click();
  await expect(page.getByLabel("Pull request number path")).toHaveValue("trigger.payload.pr_number");
  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      localStorage.setItem("automiq-theme", value);
    }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByLabel("Node inspector")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.screenshot({ path: resolve(evidenceDir, `${theme}-${width}-wait-nodes.png`), fullPage: true });
      }
    }
  }
});

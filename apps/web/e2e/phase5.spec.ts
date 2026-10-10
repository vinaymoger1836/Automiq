import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/phase5-browser");

test("owner builds an agent checkpoint and decides in the approval inbox", async ({ page }) => {
  test.setTimeout(120000);
  mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const nonce = randomUUID().slice(0, 8);
  await page.getByLabel("New workspace").fill(`Phase 5 UI ${nonce}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByLabel("Create a workflow").fill("Agent review");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(page.getByRole("heading", { name: "Agent review" })).toBeVisible();

  await page.getByRole("button", { name: /AI agent Bounded classification/ }).click();
  await expect(page.getByLabel("Model profile")).toBeVisible();
  await page.getByLabel("Instructions").fill("Classify the issue title as critical or normal.");
  await page.getByRole("button", { name: /Approval Human checkpoint/ }).click();
  await expect(page.getByLabel("Approval request")).toBeVisible();
  await page.getByLabel("Approval request").fill("Review alert");
  await page.getByLabel("Timeout (seconds)").fill("60");

  await page.getByRole("button", { name: "Remove connection start to end" }).click();
  for (const [source, target] of [["start", "agent_1"], ["agent_1", "approval_1"], ["approval_1", "end"]]) {
    await page.getByRole("combobox", { name: "From node" }).selectOption(source);
    await page.getByRole("combobox", { name: "To node" }).selectOption(target);
    await page.getByRole("button", { name: "Connect nodes" }).click();
  }
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText(/Draft saved at revision/)).toBeVisible();
  await page.getByRole("button", { name: /Publish/ }).click();
  await page.getByRole("button", { name: "Confirm publish" }).click();
  await expect(page.getByText("Version 1 published.")).toBeVisible();
  await page.getByLabel("Manual trigger payload").fill('{"title":"Production outage"}');
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("heading", { name: "Approval inbox" })).toBeVisible();
  await expect(page.locator(".approval-inbox li").filter({ hasText: "Review alert" })).toBeVisible({ timeout: 45000 });
  await expect(page.getByRole("heading", { name: "Run in progress" })).toBeVisible();

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      localStorage.setItem("automiq-theme", value);
    }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "Approval inbox" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.screenshot({ path: resolve(evidenceDir, `${theme}-${width}-approval.png`), fullPage: true });
      }
    }
  }
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 45000 });
});

import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/zoho-projects/browser");

test("owner configures a Zoho Projects task status action and sees the result", async ({ page }) => {
  test.setTimeout(120000);
  mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const nonce = randomUUID().slice(0, 8);
  await page.getByLabel("New workspace").fill(`Zoho UI ${nonce}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByLabel("Create a workflow").fill("Update Zoho task");
  await page.getByRole("button", { name: "Create workflow" }).click();

  await page.getByLabel("Provider").selectOption("zoho_projects");
  await page.locator("#integration-name").fill("Synthetic Zoho Projects");
  await page.getByLabel("Zoho data center").selectOption("com");
  await page.getByLabel("Portal ID").fill("1000001");
  await page.getByLabel("Project ID").fill("2000002");
  await page.getByLabel("OAuth client ID").fill("fake-client-id-123");
  await page.getByLabel("OAuth client secret").fill("fake-client-secret-123");
  await page.getByLabel("OAuth refresh token").fill("fake-refresh-token-123");
  await page.getByRole("button", { name: "Save integration" }).click();
  await expect(page.getByText("Integration saved.")).toBeVisible();
  await expect(page.getByLabel("OAuth refresh token")).toHaveValue("");

  await page.getByRole("button", { name: /Zoho task status Update project task/ }).click();
  await page.getByLabel("Integration", { exact: true }).selectOption({ label: "Synthetic Zoho Projects" });
  await page.getByLabel("Target task status ID").fill("3000003");
  await page.getByRole("button", { name: "Remove connection start to end" }).click();
  await page.getByRole("combobox", { name: "From node" }).selectOption("start");
  await page.getByRole("combobox", { name: "To node" }).selectOption("action_1");
  await page.getByRole("button", { name: "Connect nodes" }).click();
  await page.getByRole("combobox", { name: "From node" }).selectOption("action_1");
  await page.getByRole("combobox", { name: "To node" }).selectOption("end");
  await page.getByRole("button", { name: "Connect nodes" }).click();
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText(/Draft saved at revision/)).toBeVisible();
  await page.getByRole("button", { name: /Publish/ }).click();
  await page.getByRole("button", { name: "Confirm publish" }).click();
  await expect(page.getByText("Version 1 published.")).toBeVisible();
  await page.getByLabel("Manual trigger payload").fill('{"task_id":"4000004"}');
  await page.getByRole("button", { name: /Start run/ }).click();
  await expect(page.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 45000 });
  await expect(page.locator(".run-steps")).toContainText("action_1Attempt 2 · succeeded");

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => {
      document.documentElement.dataset.theme = value;
      localStorage.setItem("automiq-theme", value);
    }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "Integrations & triggers" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.getByRole("region", { name: "Integrations & triggers" }).screenshot({
          path: resolve(evidenceDir, `${theme}-${width}-zoho-projects.png`),
        });
      }
    }
  }
});

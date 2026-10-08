import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/phase3-studio");

test("editor builds, publishes, runs, and viewer inspects a workflow", async ({ page, browser }) => {
  test.setTimeout(120000);
  mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/studio");
  await expect(page.getByRole("button", { name: "Sign in as owner" })).toBeVisible();
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  await expect(page.getByLabel("New workspace")).toBeVisible();

  const suffix = Math.random().toString(36).slice(2, 10);
  await page.getByLabel("New workspace").fill(`Studio E2E ${suffix}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByLabel("Current workspace")).toContainText(`Studio E2E ${suffix}`);

  const editorContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const editorPage = await editorContext.newPage();
  await editorPage.goto("/studio");
  await editorPage.getByRole("button", { name: "Sign in as editor" }).click();
  await expect(editorPage.getByRole("button", { name: "Sign out" })).toBeVisible();
  const viewerContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const viewerPage = await viewerContext.newPage();
  await viewerPage.goto("/studio");
  await viewerPage.getByRole("button", { name: "Sign in as viewer" }).click();
  await expect(viewerPage.getByRole("button", { name: "Sign out" })).toBeVisible();
  await page.getByLabel("Grant workspace access").fill("editor@local.test");
  await page.getByRole("combobox", { name: "Member role" }).selectOption("editor");
  await page.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText("editor@local.test can now access this workspace.")).toBeVisible();
  await page.getByLabel("Grant workspace access").fill("viewer@local.test");
  await page.getByRole("combobox", { name: "Member role" }).selectOption("viewer");
  await page.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText("viewer@local.test can now access this workspace.")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("button", { name: "Sign in as editor" }).click();
  await page.getByLabel("Current workspace").selectOption({ label: `Studio E2E ${suffix}` });
  await expect(page.locator(".role-badge")).toHaveText("editor");
  await page.getByLabel("Create a workflow").fill("Review intake");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(page.getByRole("heading", { name: "Review intake" })).toBeVisible();

  await page.getByRole("button", { name: /HTTP action Mock action/ }).click();
  await page.getByRole("button", { name: "Remove connection start to end" }).click();
  await page.getByRole("combobox", { name: "From node" }).selectOption("start");
  await page.getByRole("combobox", { name: "To node" }).selectOption("action_1");
  await page.getByRole("button", { name: "Connect nodes" }).click();
  await page.getByRole("combobox", { name: "From node" }).selectOption("action_1");
  await page.getByRole("combobox", { name: "To node" }).selectOption("end");
  await page.getByRole("button", { name: "Connect nodes" }).click();
  await expect(page.locator(".connection-list > span")).toHaveCount(2);
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByText("Graph is ready to publish.")).toBeVisible();
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByText(/Draft saved at revision/)).toBeVisible();
  await page.getByRole("button", { name: /Publish/ }).click();
  await expect(page.getByRole("dialog", { name: "Publish workflow" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm publish" }).click();
  await expect(page.getByText("Version 1 published.")).toBeVisible();
  await page.getByRole("button", { name: "Start run" }).click();
  await expect(page.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 45000 });
  await expect(page.getByText("run / succeeded")).toBeVisible();
  await expect(page.locator(".run-steps article")).toHaveCount(3);
  await expect(page.locator(".react-flow__node")).toHaveCount(3);

  await viewerPage.reload();
  await viewerPage.getByLabel("Current workspace").selectOption({ label: `Studio E2E ${suffix}` });
  await expect(viewerPage.getByRole("heading", { name: "Review intake" })).toBeVisible();
  await expect(viewerPage.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 15000 });
  await expect(viewerPage.getByRole("button", { name: "Save draft" })).toBeDisabled();
  await expect(viewerPage.getByRole("button", { name: "Start run" })).toBeDisabled();
  await viewerContext.close();
  await editorContext.close();

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; localStorage.setItem("automiq-theme", value); }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "Review intake" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.locator(".editor-main").scrollIntoViewIfNeeded();
      await page.getByRole("button", { name: "fit view" }).click();
      const visibleNodes = await page.evaluate(() => {
        const canvas = document.querySelector(".canvas-wrap")!.getBoundingClientRect();
        return [...document.querySelectorAll(".react-flow__node")].every((node) => {
          const rect = node.getBoundingClientRect();
          return rect.right > canvas.left && rect.left < canvas.right && rect.bottom > canvas.top && rect.top < canvas.bottom;
        });
      });
      expect(visibleNodes).toBe(true);
      if (width === 375 || width === 1280) {
        await page.screenshot({ path: resolve(evidenceDir, `${theme}-${width}-canvas.png`) });
        await page.locator(".run-section").screenshot({ path: resolve(evidenceDir, `${theme}-${width}-run.png`) });
      }
    }
  }
});

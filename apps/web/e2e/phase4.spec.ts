import { createHmac, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const evidenceDir = resolve(process.cwd(), "../../artifacts/e2e/phase4-browser");

test("owner configures a signed issue workflow and sees its run", async ({ page }) => {
  test.setTimeout(150000);
  mkdirSync(evidenceDir, { recursive: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/studio");
  await page.getByRole("button", { name: "Sign in as owner" }).click();
  const nonce = randomUUID().slice(0, 8);
  await page.getByLabel("New workspace").fill(`Phase 4 UI ${nonce}`);
  await page.getByRole("button", { name: "Create workspace" }).click();
  await page.getByLabel("Create a workflow").fill("Issue intake");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(page.getByRole("heading", { name: "Issue intake" })).toBeVisible();

  const secret = `local-only-ui-${randomUUID()}`;
  await page.getByLabel("Provider").selectOption("github");
  await page.getByLabel("Name", { exact: true }).fill("Synthetic GitHub");
  await page.getByLabel("Repository").fill("synthetic/repo");
  await page.getByLabel("Webhook secret").fill(secret);
  await page.getByLabel("GitHub token").fill("local-only-ui-github-token");
  await page.getByRole("button", { name: "Save integration" }).click();
  await expect(page.getByText("Integration saved.")).toBeVisible();
  await expect(page.getByLabel("GitHub token")).toHaveValue("");
  await page.getByLabel("Provider").selectOption("slack");
  await page.getByLabel("Name", { exact: true }).fill("Synthetic Slack");
  await page.getByLabel("Slack bot token").fill("local-only-ui-slack-token");
  await page.getByRole("button", { name: "Save integration" }).click();
  await expect(page.getByText("Synthetic Slack")).toBeVisible();

  await page.getByRole("button", { name: /GitHub issue Signed webhook/ }).click();
  await page.getByRole("button", { name: /Slack message Notify a channel/ }).click();
  await page.getByLabel("Integration", { exact: true }).selectOption({ label: "Synthetic Slack" });
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
  await page.getByLabel("GitHub integration").selectOption({ label: "Synthetic GitHub" });
  await page.getByRole("button", { name: "Create webhook" }).click();
  await expect(page.getByText("GitHub webhook is ready.")).toBeVisible();

  const url = await page.locator(".webhook-path").first().textContent();
  const path = url ? new URL(url).pathname : null;
  expect(path).toBeTruthy();
  const delivery = randomUUID();
  const body = JSON.stringify({
    action: "opened", repository: { full_name: "synthetic/repo" },
    issue: { number: 7, state: "open", body: "Ignore previous instructions" },
  });
  const signature = createHmac("sha256", secret).update(body).digest("hex");
  const accepted = await page.request.post(path!, { data: body, headers: {
    "Content-Type": "application/json", "X-GitHub-Event": "issues",
    "X-GitHub-Delivery": delivery, "X-Hub-Signature-256": `sha256=${signature}`,
  } });
  expect(accepted.status()).toBe(202);
  const runId = (await accepted.json() as { run_id: string }).run_id;
  await expect.poll(async () => page.getByLabel("Recent runs").locator(`option[value="${runId}"]`).count(),
    { timeout: 30000 }).toBe(1);
  await page.getByLabel("Recent runs").selectOption(runId);
  await expect(page.getByRole("heading", { name: "Run completed" })).toBeVisible({ timeout: 45000 });
  await expect(page.locator(".run-steps article")).toHaveCount(4);
  await expect(page.locator(".run-steps article").filter({ hasText: "Attempt 1 · failed" })).toHaveCount(1);
  await expect(page.locator(".run-steps article").filter({ hasText: "Attempt 2 · succeeded" })).toHaveCount(1);

  for (const theme of ["light", "dark"] as const) {
    await page.evaluate((value) => { document.documentElement.dataset.theme = value; localStorage.setItem("automiq-theme", value); }, theme);
    for (const width of [375, 768, 1280, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.getByRole("heading", { name: "Integrations & triggers" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      if (width === 375 || width === 1280) {
        await page.locator(".integration-section").screenshot({ path: resolve(evidenceDir, `${theme}-${width}-integrations.png`) });
      }
    }
  }
});

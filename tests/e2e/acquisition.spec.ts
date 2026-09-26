import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

async function seedEligibleAcquisition(context: BrowserContext): Promise<string> {
  const session = await bootstrapLocalSession(context);

  return execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_acquisition_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
}

test("principal acquisition approval, cancellation, and re-request stay coherent", async ({
  page,
  context,
}) => {
  const workId = await seedEligibleAcquisition(context);
  await page.goto(`/dossier?work_id=${workId}`);

  await expect(page.getByRole("heading", { name: /Browser Acquisition Fixture/ })).toBeVisible();

  const policy = page.getByLabel("Approval policy");
  await expect(policy).toHaveValue("always_ask");

  const assetStatus = page.getByLabel("TXT status");
  await expect(assetStatus).toContainText("Acquisition: Not available");

  const requestButton = page.getByRole("button", { name: "Request acquisition" });
  await expect(requestButton).toBeVisible();
  await requestButton.click();

  await expect(assetStatus).toContainText("Your request: pending approval");
  const approveButton = page.getByRole("button", { name: "Approve download" });
  const cancelButton = page.getByRole("button", { name: "Cancel my request" });
  await expect(approveButton).toBeVisible();
  await expect(cancelButton).toBeVisible();

  await approveButton.click();
  await expect(assetStatus).toContainText("Your request: active");
  await expect(assetStatus).toContainText("Acquisition: queued");
  await expect(page.getByRole("button", { name: "Approve download" })).toHaveCount(0);
  await expect(cancelButton).toBeVisible();

  await cancelButton.click();
  await expect(assetStatus).toContainText("Your request: cancelled");
  await expect(assetStatus).toContainText("Acquisition: cancelled");
  await expect(page.getByRole("button", { name: "Request again" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: /Browser Acquisition Fixture/ })).toBeVisible();
  await expect(page.getByLabel("Approval policy")).toHaveValue("always_ask");
  const reloadedStatus = page.getByLabel("TXT status");
  await expect(reloadedStatus).toContainText("Your request: cancelled");
  await expect(reloadedStatus).toContainText("Acquisition: cancelled");

  await page.getByRole("button", { name: "Request again" }).click();
  await expect(reloadedStatus).toContainText("Your request: pending approval");
  await page.getByRole("button", { name: "Approve download" }).click();
  await expect(reloadedStatus).toContainText("Your request: active");
  await expect(reloadedStatus).toContainText("Acquisition: queued");

  await page.reload();
  await expect(page.getByRole("heading", { name: /Browser Acquisition Fixture/ })).toBeVisible();
  const retriedStatus = page.getByLabel("TXT status");
  await expect(retriedStatus).toContainText("Your request: active");
  await expect(retriedStatus).toContainText("Acquisition: queued");
});

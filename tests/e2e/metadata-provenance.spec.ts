import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

function seedMetadataProvenance(): string {
  return execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_metadata_provenance_seed.py"],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
}

test("dossier keeps conflicting metadata claims inspectable without raw payload leakage", async ({
  page,
}) => {
  const workId = seedMetadataProvenance();
  await page.goto(`/dossier?work_id=${workId}`);

  await expect(
    page.getByRole("heading", { name: "Browser Metadata Provenance Fixture" }),
  ).toBeVisible();

  const panel = page.getByRole("region", { name: "Metadata provenance" });
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Catalog evidence");
  await expect(panel).toContainText("browser-provider-a");
  await expect(panel).toContainText("browser-provider-b");
  await expect(panel).toContainText("100% confidence");

  const edition = panel.locator("details").nth(1);
  await edition.locator("summary").click();
  await expect(edition).toContainText("1900");
  await expect(edition).toContainText("1901");
  await expect(edition).toContainText("First Browser Press");
  await expect(edition).toContainText("Revised Browser Press");
  await expect(edition).toContainText("browser-provenance-v1");

  await expect(panel).not.toContainText("must never appear in the dossier");
  await expect(panel).not.toContainText("must also never appear in the dossier");
});

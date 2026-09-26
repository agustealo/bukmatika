import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

function seedMultiEditionDossier(): string {
  return execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_dossier_editions_seed.py"],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
}

test("dossier keeps distinct editions selectable and persists the chosen edition", async ({ page }) => {
  const workId = seedMultiEditionDossier();
  await page.goto(`/dossier?work_id=${workId}`);

  await expect(
    page.getByRole("heading", { name: "Browser Multi-Edition Dossier Fixture", level: 1 }),
  ).toBeVisible();

  const editionCards = page.locator("article.edition-card");
  await expect(editionCards).toHaveCount(2);

  const firstEdition = editionCards.filter({ hasText: "First Browser Press" });
  const revisedEdition = editionCards.filter({ hasText: "Revised Browser Press" });

  await expect(firstEdition).toContainText("1899");
  await expect(firstEdition).toContainText("EN");
  await expect(revisedEdition).toContainText("1905");
  await expect(revisedEdition).toContainText("EN");
  await expect(firstEdition.getByRole("button", { name: "Save edition" })).toBeVisible();
  await expect(revisedEdition.getByRole("button", { name: "Save edition" })).toBeVisible();

  await firstEdition.getByRole("button", { name: "Save edition" }).click();

  await expect(firstEdition.getByText("Saved", { exact: true })).toBeVisible();
  await expect(firstEdition.getByRole("button", { name: "Save edition" })).toHaveCount(0);
  await expect(revisedEdition.getByRole("button", { name: "Save edition" })).toBeVisible();

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Browser Multi-Edition Dossier Fixture", level: 1 }),
  ).toBeVisible();

  const reloadedCards = page.locator("article.edition-card");
  const reloadedFirstEdition = reloadedCards.filter({ hasText: "First Browser Press" });
  const reloadedRevisedEdition = reloadedCards.filter({ hasText: "Revised Browser Press" });

  await expect(reloadedFirstEdition.getByText("Saved", { exact: true })).toBeVisible();
  await expect(reloadedRevisedEdition.getByRole("button", { name: "Save edition" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open library" })).toBeVisible();
});

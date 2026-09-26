import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";

import { expect, test, type Locator, type Page } from "@playwright/test";

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

async function downloadText(page: Page, button: Locator): Promise<{ name: string; content: string }> {
  const [download] = await Promise.all([page.waitForEvent("download"), button.click()]);
  const path = await download.path();
  if (path === null) throw new Error("Browser citation download produced no local file");
  return {
    name: download.suggestedFilename(),
    content: await readFile(path, "utf8"),
  };
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

  const csl = await downloadText(page, firstEdition.getByRole("button", { name: "CSL JSON" }));
  expect(csl.name).toMatch(/\.json$/);
  const cslPayload = JSON.parse(csl.content) as Array<Record<string, unknown>>;
  expect(cslPayload).toHaveLength(1);
  expect(cslPayload[0]).toMatchObject({
    type: "book",
    title: "Browser Multi-Edition Dossier Fixture",
    publisher: "First Browser Press",
    language: "en",
    ISBN: "9780000018991",
    issued: { "date-parts": [[1899]] },
    author: [{ literal: "Edition Acceptance Author" }],
  });

  const bibtex = await downloadText(page, firstEdition.getByRole("button", { name: "BibTeX" }));
  expect(bibtex.name).toMatch(/\.bib$/);
  expect(bibtex.content).toContain("@book{");
  expect(bibtex.content).toContain("author = {Edition Acceptance Author}");
  expect(bibtex.content).toContain("publisher = {First Browser Press}");
  expect(bibtex.content).toContain("isbn = {9780000018991}");
  expect(bibtex.content).toContain("year = {1899}");

  const ris = await downloadText(page, firstEdition.getByRole("button", { name: "RIS" }));
  expect(ris.name).toMatch(/\.ris$/);
  expect(ris.content).toContain("TY  - BOOK");
  expect(ris.content).toContain("AU  - Edition Acceptance Author");
  expect(ris.content).toContain("PB  - First Browser Press");
  expect(ris.content).toContain("SN  - 9780000018991");
  expect(ris.content).toContain("PY  - 1899");
  expect(ris.content).toContain("ER  -");

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

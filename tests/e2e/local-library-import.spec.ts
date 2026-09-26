import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

const API_BASE_URL = "http://127.0.0.1:8000";
const BOOK_TITLE = "Browser Proven Local Notes";
const BOOK_AUTHOR = "Local Import Reader";
const BOOK_TEXT = [
  "A local book entered Bukmatika through the consumer Transfer surface.",
  "Its bytes were verified, stored canonically, processed, and opened in the Reader.",
].join("\n");

const LOCAL_BOOK = {
  name: "browser-local-notes.txt",
  mimeType: "text/plain",
  buffer: Buffer.from(BOOK_TEXT, "utf8"),
};

type LocalImportResponse = {
  work_id: string;
  edition_id: string;
  asset_id: string;
  library_entry_id: string;
};

test("a private local book stays owner-scoped while importing and re-importing idempotently", async ({
  browser,
}) => {
  const context = await browser.newContext({ baseURL: "http://127.0.0.1:3000" });
  try {
    await bootstrapLocalSession(context);
    const page = await context.newPage();
    await page.goto("/library/transfer");

    await expect(page.getByRole("heading", { name: "Import local books" })).toBeVisible();
    await page.getByTestId("local-library-files").setInputFiles(LOCAL_BOOK);
    await page.getByLabel("Author for this batch").fill(BOOK_AUTHOR);
    await page.getByLabel("Title override").fill(BOOK_TITLE);
    const importResponsePromise = page.waitForResponse(
      (response) =>
        response.url() === `${API_BASE_URL}/v1/library/import/local` &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Import selected book", exact: true }).click();
    const importResponse = await importResponsePromise;
    expect(importResponse.status()).toBe(201);
    const imported = (await importResponse.json()) as LocalImportResponse;

    await expect(page.getByText("Imported and processed into the Reader.", { exact: true })).toBeVisible();
    const readerLink = page.getByRole("link", { name: "Open in Reader" });
    await expect(readerLink).toBeVisible();
    const firstReaderHref = await readerLink.getAttribute("href");
    expect(firstReaderHref).toMatch(/^\/read\/[0-9a-f-]+\/[0-9a-f-]+$/i);

    await readerLink.click();
    await expect(page).toHaveURL(/\/read\/[0-9a-f-]+\/[0-9a-f-]+$/i);
    await expect(page.getByText(BOOK_TEXT.split("\n")[0]!, { exact: true })).toBeVisible();
    await expect(page.getByText(BOOK_TEXT.split("\n")[1]!, { exact: true })).toBeVisible();

    await page.goto("/library");
    await expect(page.getByText(BOOK_TITLE, { exact: true })).toBeVisible();

    const outsider = await browser.newContext({ baseURL: "http://127.0.0.1:3000" });
    try {
      await bootstrapLocalSession(outsider);

      const catalogResponse = await outsider.request.get(
        `${API_BASE_URL}/v1/catalog/search?q=${encodeURIComponent(BOOK_TITLE)}&limit=20`,
      );
      expect(catalogResponse.ok()).toBeTruthy();
      const catalog = (await catalogResponse.json()) as {
        items: Array<{ work_id: string; title: string }>;
      };
      expect(catalog.items.some((item) => item.work_id === imported.work_id)).toBeFalsy();
      expect(catalog.items.some((item) => item.title === BOOK_TITLE)).toBeFalsy();

      const saveWork = await outsider.request.post(
        `${API_BASE_URL}/v1/library/works/${imported.work_id}`,
      );
      expect(saveWork.status()).toBe(404);

      const saveEdition = await outsider.request.post(
        `${API_BASE_URL}/v1/library/editions/${imported.edition_id}`,
      );
      expect(saveEdition.status()).toBe(404);

      const dossier = await outsider.request.get(
        `${API_BASE_URL}/v1/dossiers/works/${imported.work_id}`,
      );
      expect(dossier.status()).toBe(404);

      const outsiderPage = await outsider.newPage();
      await outsiderPage.goto("/library");
      await expect(outsiderPage.getByText(BOOK_TITLE, { exact: true })).toHaveCount(0);
    } finally {
      await outsider.close();
    }

    await page.goto("/library/transfer");
    await page.getByTestId("local-library-files").setInputFiles(LOCAL_BOOK);
    await page.getByLabel("Author for this batch").fill("Do Not Replace Author");
    await page.getByLabel("Title override").fill("Do Not Replace Canonical Title");
    await page.getByRole("button", { name: "Import selected book", exact: true }).click();

    await expect(page.getByText("Already imported and ready to read.", { exact: true })).toBeVisible();
    await expect(page.getByText(BOOK_TITLE, { exact: true })).toBeVisible();
    await expect(page.getByText("Do Not Replace Canonical Title", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Open in Reader" })).toHaveAttribute(
      "href",
      firstReaderHref!,
    );
  } finally {
    await context.close();
  }
});

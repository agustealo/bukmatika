import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

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

test("a local book imports, processes, opens in Reader, and re-imports idempotently", async ({
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
    await page.getByRole("button", { name: "Import selected book", exact: true }).click();

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

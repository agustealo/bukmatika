import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

type FormatFixture = {
  title: string;
  library_entry_id: string;
  document_id: string;
  section_ids: [string, string, string];
  headings: [string, string, string];
  passages: [string, string, string];
};

type ReaderFormatSeed = {
  pdf: FormatFixture;
  epub: FormatFixture;
};

const PDF_HIGHLIGHT = "PDF page three proves durable progress, bookmarks, highlights, and notes.";
const PDF_NOTE = "PDF acceptance note survives canonical reload.";

async function seedReaderFormats(context: BrowserContext): Promise<ReaderFormatSeed> {
  const session = await bootstrapLocalSession(context);
  const output = execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_reader_formats_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
  return JSON.parse(output) as ReaderFormatSeed;
}

function readerUrl(fixture: FormatFixture): string {
  return `/read/${fixture.library_entry_id}/${fixture.document_id}`;
}

async function jumpReader(
  page: Page,
  label: "Go to page" | "Go to section",
  value: string,
  sectionOrdinal: number,
  sectionId: string,
) {
  await Promise.all([
    page.waitForURL(
      new RegExp(
        `\\?section=${sectionOrdinal}#reader-section-${sectionId}$`,
      ),
    ),
    page.getByLabel(label).selectOption(value),
  ]);
}

async function readingProgress(page: Page): Promise<number> {
  const label = await page.locator(".reader-progress").getAttribute("aria-label");
  const match = label?.match(/Reading progress (\d+)%/);
  return match ? Number(match[1]) : -1;
}

function highlightCard(page: Page): Locator {
  return page.locator("article.reader-highlight-item").filter({ hasText: PDF_HIGHLIGHT });
}

async function savePdfHighlight(page: Page, heading: string) {
  const paragraph = page
    .locator("section.reader-section")
    .filter({ hasText: heading })
    .locator("p.reader-paragraph")
    .filter({ hasText: PDF_HIGHLIGHT })
    .first();
  await expect(paragraph).toBeVisible();

  await paragraph.evaluate((element, selectedText) => {
    const textNode = element.firstChild;
    if (!(textNode instanceof Text)) {
      throw new Error("Expected an unannotated Reader text node.");
    }
    const text = textNode.textContent ?? "";
    const start = text.indexOf(selectedText);
    if (start < 0) throw new Error("Could not locate the PDF acceptance passage.");

    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, start + selectedText.length);
    const selection = window.getSelection();
    if (!selection) throw new Error("Browser selection API is unavailable.");
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  }, PDF_HIGHLIGHT);

  const selectionCard = page.getByLabel("Selected text annotation");
  await expect(selectionCard).toContainText(PDF_HIGHLIGHT);
  await selectionCard.getByRole("button", { name: "Save highlight" }).click();
  await expect(highlightCard(page)).toBeVisible();
}

test("PDF Reader preserves page navigation, progress, bookmark, highlight, and note state", async ({
  page,
  context,
}) => {
  const { pdf } = await seedReaderFormats(context);
  await page.goto(readerUrl(pdf));

  await expect(page.getByRole("heading", { name: pdf.headings[0] })).toBeVisible();
  const pageNavigation = page.getByLabel("Go to page");
  await expect(pageNavigation).toBeVisible();
  await expect(pageNavigation.getByRole("option", { name: "Page 1" })).toBeAttached();
  await expect(pageNavigation.getByRole("option", { name: "Page 2" })).toBeAttached();
  await expect(pageNavigation.getByRole("option", { name: "Page 3" })).toBeAttached();
  await expect(page.getByText("3 pages", { exact: true })).toBeVisible();

  await jumpReader(page, "Go to page", "page:2", 1, pdf.section_ids[1]);
  await expect(page.getByRole("heading", { name: pdf.headings[1] })).toBeVisible();
  await expect(page.getByLabel("Go to page")).toHaveValue("page:2");
  await expect.poll(() => readingProgress(page)).toBeGreaterThan(0);

  await jumpReader(page, "Go to page", "page:3", 2, pdf.section_ids[2]);
  const closingSection = page.locator("section.reader-section").filter({
    hasText: pdf.headings[2],
  });
  await expect(closingSection).toBeVisible();
  await expect.poll(() => readingProgress(page)).toBeGreaterThanOrEqual(60);

  const bookmark = closingSection.getByRole("button", { name: "Bookmark", exact: true });
  await bookmark.click();
  await expect(
    closingSection.getByRole("button", { name: "Bookmarked", exact: true }),
  ).toBeVisible();

  await savePdfHighlight(page, pdf.headings[2]);
  const savedHighlight = highlightCard(page);
  await savedHighlight.getByRole("button", { name: "Add note", exact: true }).click();
  await savedHighlight.getByLabel("Highlight note").fill(PDF_NOTE);
  await savedHighlight.getByRole("button", { name: "Save note", exact: true }).click();
  await expect(savedHighlight).toContainText(PDF_NOTE);

  await page.goto(readerUrl(pdf));
  await expect(page.getByRole("heading", { name: pdf.headings[2] })).toBeVisible();
  await expect(page.getByLabel("Go to page")).toHaveValue("page:3");
  await expect.poll(() => readingProgress(page)).toBeGreaterThanOrEqual(60);
  const resumedSection = page.locator("section.reader-section").filter({
    hasText: pdf.headings[2],
  });
  await expect(
    resumedSection.getByRole("button", { name: "Bookmarked", exact: true }),
  ).toBeVisible();
  await expect(highlightCard(page)).toContainText(PDF_NOTE);
});

test("EPUB Reader preserves spine navigation and durable resume position", async ({
  page,
  context,
}) => {
  const { epub } = await seedReaderFormats(context);
  await page.goto(readerUrl(epub));

  await expect(page.getByRole("heading", { name: epub.headings[0] })).toBeVisible();
  const sectionNavigation = page.getByLabel("Go to section");
  await expect(sectionNavigation).toBeVisible();
  await expect(
    sectionNavigation.getByRole("option", { name: epub.headings[0] }),
  ).toBeAttached();
  await expect(
    sectionNavigation.getByRole("option", { name: epub.headings[1] }),
  ).toBeAttached();
  await expect(
    sectionNavigation.getByRole("option", { name: epub.headings[2] }),
  ).toBeAttached();
  await expect(page.getByText("3 sections", { exact: true })).toBeVisible();

  await jumpReader(page, "Go to section", "spine:2", 1, epub.section_ids[1]);
  await expect(page.getByRole("heading", { name: epub.headings[1] })).toBeVisible();
  await expect(page.getByLabel("Go to section")).toHaveValue("spine:2");
  await expect.poll(() => readingProgress(page)).toBeGreaterThan(0);

  await jumpReader(page, "Go to section", "spine:3", 2, epub.section_ids[2]);
  await expect(page.getByRole("heading", { name: epub.headings[2] })).toBeVisible();
  await expect(page.getByLabel("Go to section")).toHaveValue("spine:3");
  await expect.poll(() => readingProgress(page)).toBeGreaterThanOrEqual(60);

  await page.goto(readerUrl(epub));
  await expect(page.getByRole("heading", { name: epub.headings[2] })).toBeVisible();
  await expect(page.getByLabel("Go to section")).toHaveValue("spine:3");
  await expect.poll(() => readingProgress(page)).toBeGreaterThanOrEqual(60);
  await expect(page.getByLabel("Book text")).toContainText(epub.passages[2].split("\n")[0]);
});

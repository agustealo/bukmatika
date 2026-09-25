import { execFileSync } from "node:child_process";

import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
} from "@playwright/test";

type SessionResponse = {
  principal_id: string;
  session_id: string;
  expires_at: string;
};

const NAVIGATION_PASSAGE =
  "Mariners mapped obsidian navigation routes across the old world before 1492.";
const HIGHLIGHT_TEXT = "Mariners mapped obsidian navigation routes";

async function seedOwnedResearchSources(page: Page, context: BrowserContext): Promise<void> {
  await page.goto("/research");

  await expect(
    page.getByRole("heading", { name: "Search evidence. Compare sources." }),
  ).toBeVisible();
  await expect(page.getByText("Your library is empty.")).toBeVisible();

  const sessionResponse = await context.request.get("http://127.0.0.1:8000/v1/session");
  expect(sessionResponse.ok()).toBeTruthy();
  const session = (await sessionResponse.json()) as SessionResponse;
  expect(session.principal_id).toMatch(
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );

  execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_research_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      stdio: "inherit",
    },
  );

  await page.reload();
}

async function openMatchingResearchReader(page: Page, context: BrowserContext): Promise<void> {
  await seedOwnedResearchSources(page, context);

  const source = page.getByRole("checkbox", { name: /Browser Research Evidence/ });
  await expect(source).toBeVisible();
  await source.check();

  await page
    .getByLabel("What should Bukmatika find in these books?")
    .fill("obsidian navigation");
  await page
    .locator("form.research-query")
    .getByRole("button", { name: "Search passages" })
    .click();

  await expect(page.getByText("1 passage", { exact: true })).toBeVisible();
  await expect(page.getByText(new RegExp(NAVIGATION_PASSAGE.replace(".", "\\.")))).toBeVisible();

  const citedPassage = page.getByRole("link", { name: "Open cited passage" }).first();
  await expect(citedPassage).toBeVisible();
  await citedPassage.click();

  await expect(page).toHaveURL(/\/read\//);
  await expect(page.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  await expect(page.getByLabel("Book text")).toContainText(NAVIGATION_PASSAGE);
}

function navigationHighlight(page: Page): Locator {
  return page.locator("article.reader-highlight-item").filter({ hasText: HIGHLIGHT_TEXT });
}

async function saveNavigationHighlight(page: Page): Promise<void> {
  const paragraph = page
    .locator("section.reader-section")
    .filter({ hasText: "Navigation evidence" })
    .locator("p.reader-paragraph")
    .filter({ hasText: NAVIGATION_PASSAGE })
    .first();
  await expect(paragraph).toBeVisible();

  await paragraph.evaluate((element, selectedText) => {
    const textNode = element.firstChild;
    if (!(textNode instanceof Text)) {
      throw new Error("Expected an unannotated Reader text node.");
    }
    const text = textNode.textContent ?? "";
    const start = text.indexOf(selectedText);
    if (start < 0) {
      throw new Error("Could not locate the canonical highlight passage.");
    }

    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, start + selectedText.length);
    const selection = window.getSelection();
    if (!selection) {
      throw new Error("Browser selection API is unavailable.");
    }
    selection.removeAllRanges();
    selection.addRange(range);
    element.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  }, HIGHLIGHT_TEXT);

  const selectionCard = page.getByLabel("Selected text annotation");
  await expect(selectionCard).toContainText(HIGHLIGHT_TEXT);
  await selectionCard.getByRole("button", { name: "Save highlight" }).click();

  await expect(navigationHighlight(page)).toBeVisible();
}

test("owned canonical evidence flows from Research search into the Reader", async ({
  page,
  context,
}) => {
  await openMatchingResearchReader(page, context);
});

test("source comparison keeps no-match evidence explicit and preserves Reader provenance", async ({
  page,
  context,
}) => {
  await seedOwnedResearchSources(page, context);

  await page.getByRole("button", { name: "Compare sources" }).click();

  const matchingSource = page.getByRole("checkbox", { name: /Browser Research Evidence/ });
  const noMatchSource = page.getByRole("checkbox", { name: /Browser No Match Evidence/ });
  await matchingSource.check();
  await noMatchSource.check();

  await page
    .getByLabel("What evidence should Bukmatika compare across these sources?")
    .fill("obsidian navigation");
  await page
    .locator("form.research-query")
    .getByRole("button", { name: "Compare evidence" })
    .click();

  await expect(page.getByText("2 sources compared", { exact: true })).toBeVisible();

  const comparisonSources = page.locator("section.comparison-source");
  const matchingColumn = comparisonSources.filter({ hasText: "Browser Research Evidence" });
  await expect(matchingColumn).toContainText(NAVIGATION_PASSAGE);

  const noMatchColumn = comparisonSources.filter({ hasText: "Browser No Match Evidence" });
  await expect(noMatchColumn).toContainText("No exact lexical match");
  await expect(noMatchColumn).toContainText(
    "This source stays visible so absence of matching evidence is explicit.",
  );

  const sourceLink = matchingColumn.getByRole("link", { name: "Open source" }).first();
  await expect(sourceLink).toBeVisible();
  await sourceLink.click();

  await expect(page).toHaveURL(/\/read\//);
  await expect(page.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  await expect(page.getByLabel("Book text")).toContainText(NAVIGATION_PASSAGE);
});

test("stale Reader bookmark removal is rejected across two tabs", async ({ page, context }) => {
  await openMatchingResearchReader(page, context);

  const secondPage = await context.newPage();
  await secondPage.goto(page.url());
  await expect(secondPage.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();

  const firstSection = page.locator("section.reader-section").filter({
    hasText: "Navigation evidence",
  });
  const secondSection = secondPage.locator("section.reader-section").filter({
    hasText: "Navigation evidence",
  });

  const firstBookmark = firstSection.getByRole("button", { name: "Bookmark", exact: true });
  const secondBookmark = secondSection.getByRole("button", { name: "Bookmark", exact: true });
  await expect(firstBookmark).toBeVisible();
  await expect(secondBookmark).toBeVisible();

  await firstBookmark.click();
  const firstBookmarked = firstSection.getByRole("button", { name: "Bookmarked", exact: true });
  await expect(firstBookmarked).toBeVisible();

  await secondBookmark.click();
  await expect(
    secondSection.getByRole("button", { name: "Bookmarked", exact: true }),
  ).toBeVisible();

  await firstBookmarked.click();
  await expect(
    page.getByRole("alert").filter({
      hasText: "This bookmark changed in another tab. Reload before removing it.",
    }),
  ).toContainText("This bookmark changed in another tab. Reload before removing it.");
  await expect(firstBookmarked).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  await expect(
    page
      .locator("section.reader-section")
      .filter({ hasText: "Navigation evidence" })
      .getByRole("button", { name: "Bookmarked", exact: true }),
  ).toBeVisible();

  await secondPage.close();
});

test("stale Reader highlight note keeps its draft and cannot overwrite newer state", async ({
  page,
  context,
}) => {
  await openMatchingResearchReader(page, context);
  await saveNavigationHighlight(page);

  const secondPage = await context.newPage();
  await secondPage.goto(page.url());
  await expect(secondPage.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();

  const firstHighlight = navigationHighlight(page);
  const secondHighlight = navigationHighlight(secondPage);
  await expect(firstHighlight).toBeVisible();
  await expect(secondHighlight).toBeVisible();

  await firstHighlight.getByRole("button", { name: "Add note", exact: true }).click();
  await secondHighlight.getByRole("button", { name: "Add note", exact: true }).click();

  const firstNoteInput = firstHighlight.getByLabel("Highlight note");
  const secondNoteInput = secondHighlight.getByLabel("Highlight note");
  await firstNoteInput.fill("newer note from tab A");
  await secondNoteInput.fill("stale draft from tab B");

  await firstHighlight.getByRole("button", { name: "Save note", exact: true }).click();
  await expect(firstHighlight).toContainText("newer note from tab A");
  await expect(firstHighlight.getByRole("button", { name: "Edit note", exact: true })).toBeVisible();

  await secondHighlight.getByRole("button", { name: "Save note", exact: true }).click();
  const staleMessage =
    "This highlight note changed in another tab. Your draft is still here; reload before saving again.";
  await expect(secondPage.getByRole("alert").filter({ hasText: staleMessage })).toContainText(
    staleMessage,
  );
  await expect(secondNoteInput).toHaveValue("stale draft from tab B");

  await secondPage.reload();
  await expect(secondPage.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  const reloadedHighlight = navigationHighlight(secondPage);
  await expect(reloadedHighlight).toContainText("newer note from tab A");
  await expect(reloadedHighlight).not.toContainText("stale draft from tab B");

  await secondPage.close();
});

test("stale Reader highlight removal is rejected after another tab advances its revision", async ({
  page,
  context,
}) => {
  await openMatchingResearchReader(page, context);
  await saveNavigationHighlight(page);

  const secondPage = await context.newPage();
  await secondPage.goto(page.url());
  await expect(secondPage.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();

  const staleHighlight = navigationHighlight(page);
  const newerHighlight = navigationHighlight(secondPage);
  await expect(staleHighlight).toBeVisible();
  await expect(newerHighlight).toBeVisible();

  await newerHighlight.getByRole("button", { name: "Add note", exact: true }).click();
  const newerNoteInput = newerHighlight.getByLabel("Highlight note");
  await newerNoteInput.fill("newer note before stale removal");
  await newerHighlight.getByRole("button", { name: "Save note", exact: true }).click();
  await expect(newerHighlight).toContainText("newer note before stale removal");
  await expect(newerHighlight.getByRole("button", { name: "Edit note", exact: true })).toBeVisible();

  await staleHighlight.getByRole("button", { name: "Remove", exact: true }).click();
  const staleRemovalMessage = "This highlight changed in another tab. Reload before removing it.";
  await expect(page.getByRole("alert").filter({ hasText: staleRemovalMessage })).toContainText(
    staleRemovalMessage,
  );
  await expect(staleHighlight).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  const reloadedHighlight = navigationHighlight(page);
  await expect(reloadedHighlight).toBeVisible();
  await expect(reloadedHighlight).toContainText("newer note before stale removal");

  await secondPage.close();
});

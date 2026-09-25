import { execFileSync } from "node:child_process";

import {
  expect,
  test,
  type BrowserContext,
  type Page,
} from "@playwright/test";

type SessionResponse = {
  principal_id: string;
  session_id: string;
  expires_at: string;
};

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
  await expect(
    page.getByText(/Mariners mapped obsidian navigation routes across the old world before 1492\./),
  ).toBeVisible();

  const citedPassage = page.getByRole("link", { name: "Open cited passage" }).first();
  await expect(citedPassage).toBeVisible();
  await citedPassage.click();

  await expect(page).toHaveURL(/\/read\//);
  await expect(page.getByRole("heading", { name: "Navigation evidence" })).toBeVisible();
  await expect(page.getByLabel("Book text")).toContainText(
    "Mariners mapped obsidian navigation routes across the old world before 1492.",
  );
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
  await expect(matchingColumn).toContainText(
    "Mariners mapped obsidian navigation routes across the old world before 1492.",
  );

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
  await expect(page.getByLabel("Book text")).toContainText(
    "Mariners mapped obsidian navigation routes across the old world before 1492.",
  );
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
  await expect(page.getByRole("alert")).toContainText(
    "This bookmark changed in another tab. Reload before removing it.",
  );
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

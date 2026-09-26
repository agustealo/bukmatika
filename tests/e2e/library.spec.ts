import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

type LibrarySeed = {
  titles: {
    blocked: string;
    queued: string;
    stored: string;
    ocr: string;
    failed: string;
    ready: string;
    foreign: string;
  };
  ready_library_entry_id: string;
  ready_document_id: string;
  ready_heading: string;
};

const COLLECTION_NAME = "Browser curated sources";
const TAG_NAME = "browser-priority";
const SMART_SHELF_NAME = "Browser focused shelf";

async function seedLibrary(context: BrowserContext): Promise<LibrarySeed> {
  const session = await bootstrapLocalSession(context);
  const output = execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_status_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
  return JSON.parse(output) as LibrarySeed;
}

function libraryCard(page: Page, title: string) {
  return page.locator("article.library-card").filter({ hasText: title });
}

async function openOrganizer(page: Page, title: string) {
  const card = libraryCard(page, title);
  const details = card.locator("details.library-card-organizer");
  if (!(await details.getAttribute("open"))) {
    await details.getByText("Organize", { exact: true }).click();
  }
  await expect(details).toHaveAttribute("open", "");
  return details;
}

async function openManageOrganization(page: Page) {
  const details = page.locator("details.library-manage-organizing");
  if (!(await details.getAttribute("open"))) {
    await details.getByText("Manage shelves & tags", { exact: true }).click();
  }
  await expect(details).toHaveAttribute("open", "");
  return details;
}

test("Library organization persists collections, tags, and live smart shelves", async ({
  page,
  context,
}) => {
  const seed = await seedLibrary(context);
  await page.goto("/library");

  await expect(
    page.getByRole("heading", { name: "Books you have chosen to keep." }),
  ).toBeVisible();
  await expect(page.locator("article.library-card")).toHaveCount(6);
  await expect(page.getByText(seed.titles.foreign, { exact: true })).toHaveCount(0);

  const manage = await openManageOrganization(page);
  await manage.getByLabel("New collection name").fill(COLLECTION_NAME);
  await manage.getByRole("button", { name: "Add collection" }).click();

  const collectionFilter = page.locator(".library-filter-selects").getByLabel("Collection");
  await expect(
    collectionFilter.getByRole("option", { name: `${COLLECTION_NAME} (0)`, exact: true }),
  ).toBeAttached();

  const readyOrganizer = await openOrganizer(page, seed.titles.ready);
  await readyOrganizer.getByRole("checkbox", { name: COLLECTION_NAME }).check();
  await expect(
    collectionFilter.getByRole("option", { name: `${COLLECTION_NAME} (1)`, exact: true }),
  ).toBeAttached();

  const failedOrganizer = await openOrganizer(page, seed.titles.failed);
  await failedOrganizer.getByRole("checkbox", { name: COLLECTION_NAME }).check();
  await expect(
    collectionFilter.getByRole("option", { name: `${COLLECTION_NAME} (2)`, exact: true }),
  ).toBeAttached();

  const readyTagOrganizer = await openOrganizer(page, seed.titles.ready);
  await readyTagOrganizer.getByLabel("Tag this book").fill(TAG_NAME);
  await readyTagOrganizer.getByRole("button", { name: "Tag", exact: true }).click();
  await expect(libraryCard(page, seed.titles.ready)).toContainText(`#${TAG_NAME}`);

  const failedTagOrganizer = await openOrganizer(page, seed.titles.failed);
  await failedTagOrganizer.getByRole("button", { name: `#${TAG_NAME}`, exact: true }).click();
  await expect(libraryCard(page, seed.titles.failed)).toContainText(`#${TAG_NAME}`);

  const tagFilter = page.locator(".library-filter-selects").getByLabel("Tag");
  await expect(
    tagFilter.getByRole("option", { name: `${TAG_NAME} (2)`, exact: true }),
  ).toBeAttached();

  await collectionFilter.selectOption({ label: `${COLLECTION_NAME} (2)` });
  await expect(page.locator("article.library-card")).toHaveCount(2);
  await tagFilter.selectOption({ label: `${TAG_NAME} (2)` });
  await expect(page.locator("article.library-card")).toHaveCount(2);
  await expect(libraryCard(page, seed.titles.ready)).toBeVisible();
  await expect(libraryCard(page, seed.titles.failed)).toBeVisible();

  const manageFiltered = await openManageOrganization(page);
  await manageFiltered.getByRole("button", { name: "Use current filters" }).click();
  await manageFiltered.getByLabel("Smart shelf name").fill(SMART_SHELF_NAME);
  await manageFiltered.getByRole("button", { name: "Create smart shelf" }).click();

  const smartShelfButton = page
    .getByLabel("Smart shelves")
    .getByRole("button", { name: new RegExp(`^${SMART_SHELF_NAME} · 2$`) });
  await expect(smartShelfButton).toBeVisible();
  await smartShelfButton.click();
  await expect(page.getByRole("heading", { name: SMART_SHELF_NAME })).toBeVisible();
  await expect(page.locator("article.library-card")).toHaveCount(2);

  const failedInShelf = await openOrganizer(page, seed.titles.failed);
  await failedInShelf.getByTitle(`Remove tag ${TAG_NAME}`).click();

  await expect(page.locator("article.library-card")).toHaveCount(1);
  await expect(libraryCard(page, seed.titles.ready)).toBeVisible();
  await expect(libraryCard(page, seed.titles.failed)).toHaveCount(0);
  await expect(
    page
      .getByLabel("Smart shelves")
      .getByRole("button", { name: new RegExp(`^${SMART_SHELF_NAME} · 1$`) }),
  ).toBeVisible();

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Books you have chosen to keep." }),
  ).toBeVisible();
  await expect(page.locator("article.library-card")).toHaveCount(6);

  const persistedShelf = page
    .getByLabel("Smart shelves")
    .getByRole("button", { name: new RegExp(`^${SMART_SHELF_NAME} · 1$`) });
  await expect(persistedShelf).toBeVisible();
  await persistedShelf.click();
  await expect(page.locator("article.library-card")).toHaveCount(1);

  const readyCard = libraryCard(page, seed.titles.ready);
  await readyCard.getByRole("link", { name: "Read" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/read/${seed.ready_library_entry_id}/${seed.ready_document_id}$`),
  );
  await expect(page.getByRole("heading", { name: seed.ready_heading })).toBeVisible();
});

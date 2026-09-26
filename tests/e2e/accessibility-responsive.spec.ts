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

async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(dimensions.document).toBeLessThanOrEqual(dimensions.viewport);
  expect(dimensions.body).toBeLessThanOrEqual(dimensions.viewport);
}

async function assertVisibleFocus(locator: Locator): Promise<void> {
  await expect(locator).toBeFocused();
  const focus = await locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth),
    };
  });
  expect(focus.outlineStyle).not.toBe("none");
  expect(focus.outlineWidth).toBeGreaterThan(0);
}

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

test("Discovery is keyboard-operable with visible focus and labeled controls", async ({
  page,
  context,
}) => {
  await bootstrapLocalSession(context);
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  await expect(page.locator("main")).toHaveCount(1);
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  const query = page.getByLabel("What do you want to build a library about?");
  await expect(query).toBeVisible();

  await page.keyboard.press("Tab");
  await assertVisibleFocus(page.getByRole("link", { name: "Bukmatika home" }));

  await page.keyboard.press("Tab");
  await assertVisibleFocus(
    page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", {
      name: "Discover",
      exact: true,
    }),
  );

  for (let index = 0; index < 5; index += 1) {
    await page.keyboard.press("Tab");
  }
  await expect(query).toBeFocused();
  await page.keyboard.type("maritime astronomy");

  await page.keyboard.press("Tab");
  await assertVisibleFocus(page.getByRole("button", { name: "Discover", exact: true }));

  await page.keyboard.press("Tab");
  const rankingSummary = page.locator("details").filter({ hasText: "Tune ranking" }).locator("summary");
  await assertVisibleFocus(rankingSummary);
  await page.keyboard.press("Enter");
  await expect(page.locator("details").filter({ hasText: "Tune ranking" })).toHaveJSProperty(
    "open",
    true,
  );

  await expect(page.getByLabel("Language")).toBeVisible();
  await expect(page.getByLabel("Format")).toBeVisible();
  await expect(page.getByLabel("Rights state")).toBeVisible();
  await expect(page.getByLabel("Source")).toBeVisible();
  await expect(page.getByLabel("Preferred start year")).toBeVisible();
  await expect(page.getByLabel("Preferred end year")).toBeVisible();
});

test("consumer shells stay within mobile width and honor reduced motion", async ({
  page,
  context,
}) => {
  await bootstrapLocalSession(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });

  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await expect(page.getByRole("navigation", { name: "Primary navigation" })).toBeVisible();
  const mastheadDirection = await page.locator(".masthead").evaluate(
    (element) => getComputedStyle(element).flexDirection,
  );
  expect(mastheadDirection).toBe("column");
  const navTransition = await page
    .getByRole("navigation", { name: "Primary navigation" })
    .getByRole("link", { name: "Discover", exact: true })
    .evaluate((element) => getComputedStyle(element).transitionDuration);
  expect(navTransition).toBe("0s");

  for (const route of [
    "/library",
    "/library/transfer",
    "/status",
    "/research",
    "/personalization",
  ]) {
    await page.goto(route);
    await expect(page.locator("main")).toHaveCount(1);
    await assertNoHorizontalOverflow(page);
  }
});

test("Reader remains usable without horizontal overflow at phone and tablet widths", async ({
  page,
  context,
}) => {
  const { pdf } = await seedReaderFormats(context);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(readerUrl(pdf));
  await expect(page.getByRole("heading", { name: pdf.headings[0] })).toBeVisible();
  await expect(page.getByLabel("Go to page")).toBeVisible();
  await expect(page.getByLabel("Book text")).toContainText(pdf.passages[0].split("\n")[0]);
  await assertNoHorizontalOverflow(page);
  expect(
    await page.locator(".reader-sidebar").evaluate((element) => getComputedStyle(element).position),
  ).toBe("static");

  await page.setViewportSize({ width: 768, height: 1024 });
  await page.reload();
  await expect(page.getByRole("heading", { name: pdf.headings[0] })).toBeVisible();
  await expect(page.getByLabel("Go to page")).toBeVisible();
  await assertNoHorizontalOverflow(page);
  expect(
    await page.locator(".reader-sidebar").evaluate((element) => getComputedStyle(element).position),
  ).toBe("static");
});

import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Locator, type Page } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

type FormatFixture = {
  library_entry_id: string;
  document_id: string;
  heading: string;
  expected_navigation_labels: string[];
};

type ReaderFormatSeed = {
  pdf: FormatFixture;
  epub: FormatFixture;
};

async function assertVisibleFocus(locator: Locator) {
  await expect(locator).toBeFocused();
  const focus = await locator.evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth || "0"),
      boxShadow: style.boxShadow,
    };
  });
  expect(
    focus.outlineStyle !== "none" && focus.outlineWidth > 0 || focus.boxShadow !== "none",
    "Focused control should have a visible outline or focus shadow.",
  ).toBeTruthy();
}

function assertNoHorizontalOverflow(page: Page) {
  return expect
    .poll(async () =>
      page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      })),
    )
    .toEqual(
      expect.objectContaining({
        scrollWidth: expect.any(Number),
        clientWidth: expect.any(Number),
      }),
    );
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

  for (let index = 0; index < 4; index += 1) {
    await page.keyboard.press("Tab");
  }
  await assertVisibleFocus(
    page.getByRole("navigation", { name: "Primary navigation" }).getByRole("link", {
      name: "AI",
      exact: true,
    }),
  );

  await page.keyboard.press("Tab");
  await assertVisibleFocus(page.getByRole("link", { name: "Import your local library instead." }));

  await page.keyboard.press("Tab");
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

test("consumer shells stay within mobile width and honor reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  const homeMetrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(homeMetrics.scrollWidth).toBeLessThanOrEqual(homeMetrics.clientWidth);

  await expect(page.locator(".top-nav")).toHaveCSS("overflow-x", "auto");
  const homeAnimationDuration = await page.locator("body").evaluate((element) =>
    getComputedStyle(element).animationDuration,
  );
  expect(homeAnimationDuration).toBe("0.01ms");

  await page.goto("/library");
  const libraryMetrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(libraryMetrics.scrollWidth).toBeLessThanOrEqual(libraryMetrics.clientWidth);

  await page.goto("/research");
  const researchMetrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(researchMetrics.scrollWidth).toBeLessThanOrEqual(researchMetrics.clientWidth);
});

test("Reader remains usable without horizontal overflow at phone and tablet widths", async ({
  page,
  context,
}) => {
  const seed = await seedReaderFormats(context);

  for (const width of [390, 768]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1024 });
    await page.goto(readerUrl(seed.pdf));
    await expect(page.getByRole("heading", { name: seed.pdf.heading })).toBeVisible();
    await assertNoHorizontalOverflow(page);
  }
});

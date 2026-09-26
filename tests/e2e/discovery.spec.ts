import { expect, test, type BrowserContext, type Page } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

async function openDiscovery(page: Page, context: BrowserContext): Promise<void> {
  await bootstrapLocalSession(context);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Ask for a body of knowledge, not a filename." }),
  ).toBeVisible();
  await expect(page.getByLabel("What do you want to build a library about?")).toBeVisible();
}

test("Discovery validates preferences and resolves live rights-aware source results", async ({
  page,
  context,
}) => {
  await openDiscovery(page, context);

  const query = page.getByLabel("What do you want to build a library about?");
  await query.fill("Pride and Prejudice");

  const preferences = page.locator("details").filter({ hasText: "Tune ranking" });
  await preferences.locator("summary").click();
  await expect(preferences).toHaveJSProperty("open", true);

  await page.getByLabel("Preferred start year").fill("1900");
  await page.getByLabel("Preferred end year").fill("1800");
  await page.getByRole("button", { name: "Discover" }).click();
  await expect(
    page.locator(".error-card").filter({
      hasText: "Preferred start year cannot be later than the preferred end year.",
    }),
  ).toBeVisible();

  await page.getByLabel("Preferred start year").fill("1790");
  await page.getByLabel("Preferred end year").fill("1900");
  await page.getByLabel("Language").fill("en");
  await page.getByLabel("Format").selectOption("PDF");
  await page.getByLabel("Rights state").selectOption("public_domain");
  await page.getByLabel("Source").selectOption("project_gutenberg");

  await expect(preferences.locator("summary")).toContainText("5 set");
  await page.getByRole("button", { name: "Discover" }).click();

  const results = page.locator(".results-wrap");
  await expect(results).toBeVisible({ timeout: 25_000 });
  await expect(results.locator(".results-meta strong")).toContainText(/\d+ results?/);
  await expect(results.getByText("Preference boost ≤ 0.05", { exact: true })).toBeVisible();
  await expect(results.locator(".results-meta")).toContainText("project_gutenberg");

  const cards = results.locator("article.book-card");
  await expect(cards.first()).toBeVisible();
  expect(await cards.count()).toBeGreaterThan(0);

  const firstCard = cards.first();
  await expect(firstCard.locator("h2")).not.toHaveText("");
  await expect(firstCard.locator(".rights-chip")).toBeVisible();
  await expect(firstCard.locator(".source-label")).toBeVisible();
  await expect(firstCard.locator("p").filter({ hasText: /Neutral \d/ })).toBeVisible();

  const sourceLink = firstCard.getByRole("link", { name: /^Source/ });
  await expect(sourceLink).toHaveAttribute("href", /^https?:\/\//);
  await expect(sourceLink).toHaveAttribute("target", "_blank");

  const dossierLink = firstCard.getByRole("link", { name: "View dossier" });
  await expect(dossierLink).toHaveAttribute(
    "href",
    /^\/dossier\?provider=[^&]+&record_id=.+$/,
  );
  await dossierLink.click();

  await expect(page).toHaveURL(/\/dossier\?provider=[^&]+&record_id=.+/);
  await expect(page.getByText("Canonical work dossier", { exact: true })).toBeVisible();
  await expect(page.locator("section.edition-list")).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Dossier failed" })).toHaveCount(0);
});

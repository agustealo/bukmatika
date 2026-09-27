import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

async function seedUnprocessedBook(context: BrowserContext): Promise<void> {
  const session = await bootstrapLocalSession(context);
  execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_onboarding_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  );
}

test("first-run guidance follows an actually empty library", async ({ page, context }) => {
  await bootstrapLocalSession(context);
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Build the first shelf from a question." }),
  ).toBeVisible();
  await expect(page.getByText("Search the open-book web below.", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Import your local library instead." })).toHaveAttribute(
    "href",
    "/library/transfer",
  );
});

test("saved books without processed text hand off to Research readiness guidance", async ({
  page,
  context,
}) => {
  await seedUnprocessedBook(context);

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Build the first shelf from a question." }),
  ).toHaveCount(0);

  await page.goto("/research");
  await expect(
    page.getByRole("heading", { name: "Your books are not research-ready yet." }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Check processing status" })).toHaveAttribute(
    "href",
    "/status",
  );
  await expect(page.getByRole("link", { name: "Import local books" })).toHaveAttribute(
    "href",
    "/library/transfer",
  );
  await expect(page.getByText("Your library is empty.", { exact: true })).toHaveCount(0);
});

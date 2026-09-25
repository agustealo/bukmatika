import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

type SessionResponse = {
  principal_id: string;
  session_id: string;
  expires_at: string;
};

test("owned canonical evidence flows from Research search into the Reader", async ({
  page,
  context,
}) => {
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
});

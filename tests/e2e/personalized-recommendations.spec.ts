import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";


test("personalized discovery explains deterministic fit without model authority", async ({
  page,
  context,
}) => {
  const session = await bootstrapLocalSession(context);
  execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_recommendations_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      stdio: "inherit",
    },
  );

  const profileResponse = await context.request.get(
    "http://127.0.0.1:8000/v1/personalization",
  );
  expect(profileResponse.ok()).toBeTruthy();
  const profile = (await profileResponse.json()) as { ai_enabled: boolean };
  expect(profile.ai_enabled).toBe(false);

  await page.goto("/");

  const recommendationHeading = page.getByRole("heading", {
    name: "Why these books may fit you",
  });
  const surface = page.locator("section").filter({ has: recommendationHeading });
  await expect(surface).toBeVisible();
  await expect(
    surface.getByText(
      "This is a separate, explainable view over Bukmatika's persisted catalog. It does not alter live search, rights decisions, or acquisition eligibility.",
      { exact: true },
    ),
  ).toBeVisible();

  const best = surface
    .getByRole("heading", { name: "Browser Maritime Recommendation", exact: true })
    .locator("..");
  await expect(best).toBeVisible();
  await expect(best.getByText("Preference fit 1.50", { exact: true })).toBeVisible();

  const subjectReason = best.locator("li").filter({ hasText: "Subject" });
  await expect(subjectReason.getByText("Subject", { exact: true })).toBeVisible();
  await expect(subjectReason.getByText("Maritime History", { exact: true })).toBeVisible();
  await expect(subjectReason.getByText("explicit", { exact: true })).toBeVisible();
  await expect(subjectReason.getByText("+1.00", { exact: true })).toBeVisible();

  const formatReason = best.locator("li").filter({ hasText: "Format" });
  await expect(formatReason.getByText("Format", { exact: true })).toBeVisible();
  await expect(formatReason.getByText("EPUB", { exact: true })).toBeVisible();
  await expect(formatReason.getByText("explicit", { exact: true })).toBeVisible();
  await expect(formatReason.getByText("+0.50", { exact: true })).toBeVisible();

  const formatOnly = surface
    .getByRole("heading", { name: "Browser Astronomy EPUB", exact: true })
    .locator("..");
  await expect(formatOnly).toBeVisible();
  await expect(formatOnly.getByText("Preference fit 0.50", { exact: true })).toBeVisible();
  await expect(formatOnly.getByText("Astronomy", { exact: true })).toHaveCount(0);

  await expect(surface.getByText("Browser Owned Maritime Book", { exact: true })).toHaveCount(0);

  const dossierLink = best.getByRole("link", { name: "View dossier" });
  await expect(dossierLink).toHaveAttribute("href", /^\/dossier\?work_id=[0-9a-f-]+$/i);
  await dossierLink.click();

  await expect(page).toHaveURL(/\/dossier\?work_id=[0-9a-f-]+$/i);
  await expect(page.getByText("Canonical work dossier", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Browser Maritime Recommendation", exact: true }),
  ).toBeVisible();
});

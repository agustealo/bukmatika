import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

type SessionResponse = {
  principal_id: string;
  session_id: string;
  expires_at: string;
};

type StatusSeed = {
  titles: {
    blocked: string;
    queued: string;
    stored: string;
    ocr: string;
    failed: string;
    ready: string;
    foreign: string;
  };
  blocked_work_id: string;
  ready_library_entry_id: string;
  ready_document_id: string;
  ready_heading: string;
};

async function seedStatusCenter(page: Page, context: BrowserContext): Promise<StatusSeed> {
  await page.goto("/research");
  await expect(
    page.getByRole("heading", { name: "Search evidence. Compare sources." }),
  ).toBeVisible();

  const sessionResponse = await context.request.get("http://127.0.0.1:8000/v1/session");
  expect(sessionResponse.ok()).toBeTruthy();
  const session = (await sessionResponse.json()) as SessionResponse;

  const output = execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_status_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
  return JSON.parse(output) as StatusSeed;
}

function statusCard(page: Page, title: string) {
  return page.locator("article.status-card").filter({ hasText: title });
}

async function chooseFilter(page: Page, label: string, count: number) {
  const button = page.getByRole("button", { name: new RegExp(label, "i") });
  await expect(button).toContainText(String(count));
  await button.click();
  await expect(button).toHaveAttribute("aria-pressed", "true");
}

test("Status Center projects canonical pipeline state, filters it, and hands ready work to Reader", async ({
  page,
  context,
}) => {
  const seed = await seedStatusCenter(page, context);
  await page.goto("/status");

  await expect(
    page.getByRole("heading", { name: "What is ready, moving, blocked, or broken." }),
  ).toBeVisible();

  const all = page.getByRole("button", { name: /All/i });
  const failed = page.getByRole("button", { name: /Failed/i });
  const needsAttention = page.getByRole("button", { name: /Needs attention/i });
  const inProgress = page.getByRole("button", { name: /In progress/i });
  const ready = page.getByRole("button", { name: /Ready/i });

  await expect(all).toContainText("6");
  await expect(failed).toContainText("1");
  await expect(needsAttention).toContainText("2");
  await expect(inProgress).toContainText("2");
  await expect(ready).toContainText("1");
  await expect(page.locator("article.status-card")).toHaveCount(6);
  await expect(page.getByText(seed.titles.foreign, { exact: true })).toHaveCount(0);

  const blockedCard = statusCard(page, seed.titles.blocked);
  await expect(blockedCard).toContainText("RIGHTS_BLOCKED");
  await expect(blockedCard).toContainText("restricted");

  const queuedCard = statusCard(page, seed.titles.queued);
  await expect(queuedCard).toContainText("ACQUISITION_IN_PROGRESS");
  await expect(queuedCard).toContainText("queued");

  const storedCard = statusCard(page, seed.titles.stored);
  await expect(storedCard).toContainText("PROCESSING_REQUIRED");

  const ocrCard = statusCard(page, seed.titles.ocr);
  await expect(ocrCard).toContainText("OCR_IN_PROGRESS");
  await expect(ocrCard).toContainText("running");

  const failedCard = statusCard(page, seed.titles.failed);
  await expect(failedCard).toContainText("REMOTE_DOWNLOAD_FAILED");

  const readyCard = statusCard(page, seed.titles.ready);
  await expect(readyCard).toContainText("DOCUMENT_READY");
  await expect(readyCard.getByRole("link", { name: "Read" })).toBeVisible();

  await chooseFilter(page, "Failed", 1);
  await expect(page.locator("article.status-card")).toHaveCount(1);
  await expect(statusCard(page, seed.titles.failed)).toBeVisible();

  await chooseFilter(page, "Needs attention", 2);
  await expect(page.locator("article.status-card")).toHaveCount(2);
  await expect(statusCard(page, seed.titles.blocked)).toBeVisible();
  await expect(statusCard(page, seed.titles.stored)).toBeVisible();

  await chooseFilter(page, "In progress", 2);
  await expect(page.locator("article.status-card")).toHaveCount(2);
  await expect(statusCard(page, seed.titles.queued)).toBeVisible();
  await expect(statusCard(page, seed.titles.ocr)).toBeVisible();

  await chooseFilter(page, "Ready", 1);
  await expect(page.locator("article.status-card")).toHaveCount(1);
  const filteredReadyCard = statusCard(page, seed.titles.ready);
  await expect(filteredReadyCard).toBeVisible();

  await page.reload();
  await expect(
    page.getByRole("heading", { name: "What is ready, moving, blocked, or broken." }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /All/i })).toContainText("6");
  await expect(page.locator("article.status-card")).toHaveCount(6);

  await statusCard(page, seed.titles.blocked).getByRole("link", { name: "Details" }).click();
  await expect(page).toHaveURL(new RegExp(`/dossier\\?work_id=${seed.blocked_work_id}$`));
  await expect(page.getByRole("heading", { name: seed.titles.blocked })).toBeVisible();

  await page.goto("/status");
  await chooseFilter(page, "Ready", 1);
  await statusCard(page, seed.titles.ready).getByRole("link", { name: "Read" }).click();
  await expect(page).toHaveURL(
    new RegExp(`/read/${seed.ready_library_entry_id}/${seed.ready_document_id}$`),
  );
  await expect(page.getByRole("heading", { name: seed.ready_heading })).toBeVisible();
  await expect(page.getByText(/ready-state handoff opens canonical processed reader evidence/i)).toBeVisible();
});

import { execFileSync } from "node:child_process";

import { expect, test, type BrowserContext } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

type BackupSeed = {
  title: string;
  collection: string;
  tag: string;
  smart_shelf: string;
  library_entry_id: string;
};

async function seedBackupSource(context: BrowserContext): Promise<BackupSeed> {
  const session = await bootstrapLocalSession(context);
  const output = execFileSync(
    process.env.PYTHON ?? "python",
    ["services/api/tests/browser_backup_restore_seed.py", session.principal_id],
    {
      cwd: process.cwd(),
      env: process.env,
      encoding: "utf8",
    },
  ).trim();
  return JSON.parse(output) as BackupSeed;
}

test("a Bukmatika backup restores portable library state into a fresh profile", async ({ browser }) => {
  const sourceContext = await browser.newContext({
    baseURL: "http://127.0.0.1:3000",
    acceptDownloads: true,
  });
  const destinationContext = await browser.newContext({
    baseURL: "http://127.0.0.1:3000",
  });

  try {
    const seed = await seedBackupSource(sourceContext);
    const sourcePage = await sourceContext.newPage();
    await sourcePage.goto("/library/transfer");
    await expect(sourcePage.getByRole("heading", { name: "Back up or restore your library." })).toBeVisible();

    const downloadPromise = sourcePage.waitForEvent("download");
    await sourcePage.getByRole("button", { name: "Download backup" }).click();
    const download = await downloadPromise;
    const backupPath = await download.path();
    expect(backupPath).not.toBeNull();
    await expect(sourcePage.getByText(/0 omitted by current export policy/)).toBeVisible();

    await bootstrapLocalSession(destinationContext);
    const destinationPage = await destinationContext.newPage();
    await destinationPage.goto("/library/transfer");
    await expect(destinationPage.getByRole("heading", { name: "Back up or restore your library." })).toBeVisible();

    await destinationPage.getByLabel("Backup file").setInputFiles(backupPath!);
    await destinationPage.getByRole("button", { name: "Review backup" }).click();

    await expect(destinationPage.getByRole("heading", { name: "Ready to restore" })).toBeVisible();
    await expect(destinationPage.getByText("1 library entries", { exact: true })).toBeVisible();
    await expect(destinationPage.getByText("0 conflicts", { exact: true })).toBeVisible();

    await destinationPage.getByRole("button", { name: "Restore reviewed backup" }).click();
    await expect(destinationPage.getByRole("heading", { name: "Backup restored" })).toBeVisible();
    await expect(destinationPage.getByText("1 entries created", { exact: true })).toBeVisible();

    await destinationPage.goto("/library");
    await expect(destinationPage.getByText(seed.title, { exact: true })).toBeVisible();

    const collectionFilter = destinationPage.locator(".library-filter-selects").getByLabel("Collection");
    await expect(
      collectionFilter.getByRole("option", { name: `${seed.collection} (1)`, exact: true }),
    ).toBeAttached();

    const tagFilter = destinationPage.locator(".library-filter-selects").getByLabel("Tag");
    await expect(
      tagFilter.getByRole("option", { name: `${seed.tag} (1)`, exact: true }),
    ).toBeAttached();

    await expect(
      destinationPage
        .getByLabel("Smart shelves")
        .getByRole("button", { name: new RegExp(`^${seed.smart_shelf} · 1$`) }),
    ).toBeVisible();
  } finally {
    await sourceContext.close();
    await destinationContext.close();
  }
});

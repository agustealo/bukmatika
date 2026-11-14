import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

test("WordPress manager installs, stores redacted REST settings, and revokes on uninstall", async ({
  page,
  context,
}) => {
  await bootstrapLocalSession(context);
  await page.goto("/plugins");

  await expect(page.getByRole("heading", { name: "WordPress connection" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary navigation" })
    .getByRole("link", { name: "Plugins" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByText("Publishing and direct database access are not enabled.")).toBeVisible();
  await page.getByRole("button", { name: "Install", exact: true }).click();

  await expect(page.getByRole("heading", { name: "REST connection settings" })).toBeVisible();
  await page.getByLabel("Site URL").fill("https://example.com");
  await page.getByLabel("WordPress username").fill("editor");
  await page.getByLabel("Application Password").fill("secret-app-password");
  await page.getByRole("button", { name: "Save connection" }).click();

  await expect(page.getByText(/Previous verification evidence has been invalidated/)).toBeVisible();
  await expect(page.getByLabel("Site URL")).toHaveValue("https://example.com");
  await expect(page.getByLabel("WordPress username")).toHaveValue("editor");
  await expect(page.getByLabel("Application Password")).toHaveValue("");
  await expect(page.getByText("not_tested")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Inspect capabilities" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Enable read-only access" })).toBeVisible();
  await page.getByRole("button", { name: "Enable read-only access" }).click();
  await expect(page.getByRole("button", { name: "Disable read access" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Browse posts" })).toBeVisible();
  await page.getByRole("button", { name: "Disable read access" }).click();
  await expect(page.getByRole("button", { name: "Enable read-only access" })).toBeVisible();

  page.once("dialog", (dialog) => void dialog.accept());
  await page.getByRole("button", { name: "Uninstall WordPress" }).click();

  await expect(page.getByRole("button", { name: "Install", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "REST connection settings" })).toHaveCount(0);
});

test("WordPress manager fits a mobile viewport without enabling publishing", async ({
  page,
  context,
}) => {
  await bootstrapLocalSession(context);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/plugins");
  await expect(page.getByRole("heading", { name: "WordPress connection" })).toBeVisible();
  const widths = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    viewport: document.documentElement.clientWidth,
  }));
  expect(widths.page).toBeLessThanOrEqual(widths.viewport);
  await expect(page.getByRole("button", { name: /publish/i })).toHaveCount(0);
});

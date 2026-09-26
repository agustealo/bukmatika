import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";


type AccountExport = {
  schema_version: number;
  export_kind: string;
  principal: {
    principal_id: string;
    external_subject: string;
  };
  operational_records: Array<{
    record_type: string;
    data: Record<string, unknown>;
  }>;
};

type AccountDeleteResponse = {
  principal_deleted: boolean;
  interaction_events_deleted: number;
  private_local_imports_deleted: number;
  storage_objects_queued: number;
};


test("whole-account export and erasure stay distinct from personalization reset", async ({
  page,
  context,
}) => {
  const originalSession = await bootstrapLocalSession(context);

  const exportResponse = await context.request.get(
    "http://127.0.0.1:8000/v1/personalization/account/export",
  );
  expect(exportResponse.ok()).toBeTruthy();
  const exported = (await exportResponse.json()) as AccountExport;
  expect(exported.schema_version).toBe(1);
  expect(exported.export_kind).toBe("bukmatika-account-data");
  expect(exported.principal.principal_id).toBe(originalSession.principal_id);
  expect(exported.principal.external_subject.length).toBeGreaterThan(0);

  const serializedExport = JSON.stringify(exported);
  expect(serializedExport).not.toContain("token_sha256");
  expect(serializedExport).not.toContain("claim_token");

  await page.goto("/personalization");
  await expect(page.getByRole("heading", { name: "Your data and privacy" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Reset what Bukmatika knows about me" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Delete my Bukmatika account data" }),
  ).toBeVisible();

  const deleteInput = page.getByLabel(/Type DELETE to confirm/i);
  const deleteButton = page.getByRole("button", { name: "Delete account data" });
  await expect(deleteButton).toBeDisabled();
  await deleteInput.fill("DELETE");
  await expect(deleteButton).toBeEnabled();

  const deletionResponsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/v1/personalization/account/delete") &&
      response.request().method() === "POST",
  );
  await deleteButton.click();
  const deletionResponse = await deletionResponsePromise;
  expect(deletionResponse.ok()).toBeTruthy();
  const deletion = (await deletionResponse.json()) as AccountDeleteResponse;
  expect(deletion.principal_deleted).toBe(true);
  expect(deletion.interaction_events_deleted).toBeGreaterThanOrEqual(0);
  expect(deletion.private_local_imports_deleted).toBeGreaterThanOrEqual(0);
  expect(deletion.storage_objects_queued).toBeGreaterThanOrEqual(0);

  await page.waitForURL("/");
  const replacementSession = await bootstrapLocalSession(context);
  expect(replacementSession.principal_id).not.toBe(originalSession.principal_id);

  const replacementExport = await context.request.get(
    "http://127.0.0.1:8000/v1/personalization/account/export",
  );
  expect(replacementExport.ok()).toBeTruthy();
  const replacement = (await replacementExport.json()) as AccountExport;
  expect(replacement.principal.principal_id).toBe(replacementSession.principal_id);
  expect(replacement.principal.principal_id).not.toBe(originalSession.principal_id);
});

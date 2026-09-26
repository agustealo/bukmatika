import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

type PrincipalDataExport = {
  schema_version: number;
  export_kind: string;
  snapshot_mode: string;
  coverage: {
    library_metadata_and_state: boolean;
    personalization_and_ai_state: boolean;
    book_bytes_included: boolean;
    book_bytes_export_route: string;
  };
  library: {
    export_kind: string;
    content_mode: string;
    entries: unknown[];
  };
  personalization: {
    schema_version: number;
    user_model: {
      user_model_id: string;
    };
  };
};

test("principal data export downloads the authenticated canonical envelope", async ({
  page,
  context,
}) => {
  const session = await bootstrapLocalSession(context);
  await page.goto("/personalization");

  await expect(page.getByRole("heading", { name: "Your data & privacy" })).toBeVisible();
  const exportButton = page.getByRole("button", { name: "Download data export" });
  await expect(exportButton).toBeVisible();

  const downloadPromise = page.waitForEvent("download");
  await exportButton.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^bukmatika-data-\d{4}-\d{2}-\d{2}\.json$/);

  const path = await download.path();
  expect(path).not.toBeNull();
  if (path === null) {
    throw new Error("Expected privacy export download path.");
  }
  const payload = JSON.parse(readFileSync(path, "utf8")) as PrincipalDataExport;

  expect(payload.schema_version).toBe(1);
  expect(payload.export_kind).toBe("bukmatika-principal-data");
  expect(payload.snapshot_mode).toBe("component-snapshots");
  expect(payload.coverage).toEqual({
    library_metadata_and_state: true,
    personalization_and_ai_state: true,
    book_bytes_included: false,
    book_bytes_export_route: "/v1/library/export/file",
  });
  expect(payload.library.export_kind).toBe("bukmatika-library-manifest");
  expect(payload.library.content_mode).toBe("metadata-and-state-only");
  expect(payload.library.entries).toEqual([]);
  expect(payload.personalization.schema_version).toBe(1);
  expect(payload.personalization.user_model.user_model_id).toBeTruthy();
  expect(payload.personalization.user_model.user_model_id).not.toBe(session.principal_id);
});

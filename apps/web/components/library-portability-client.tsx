"use client";

import { ChangeEvent, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./library-portability.module.css";

const BUNDLE_MEDIA_TYPE = "application/vnd.bukmatika.library+zip";

type ImportConflict = {
  target: string;
  source_id: string;
  code: string;
  detail: string;
};

type BundleByte = {
  source_library_entry_id: string;
  source_asset_id: string;
  member_name: string;
  format: string;
  media_type: string | null;
  sha256: string;
  byte_size: number;
};

type BundleOmission = {
  source_library_entry_id: string;
  source_asset_id: string;
  code: string;
  detail: string;
};

type BundlePlan = {
  schema_version: 1;
  mode: "file-dry-run";
  plan: {
    can_apply: boolean;
    entries: unknown[];
    collections: unknown[];
    tags: unknown[];
    smart_shelves: unknown[];
    conflicts: ImportConflict[];
  };
  included_bytes: BundleByte[];
  omitted_bytes: BundleOmission[];
};

type ByteApplyResult = {
  source_library_entry_id: string;
  source_asset_id: string;
  status: "stored" | "idempotent" | "denied" | "conflict" | "integrity-error";
  code: string | null;
  detail: string | null;
};

type BundleApplyResult = {
  schema_version: 1;
  mode: "file-apply";
  manifest: {
    committed: boolean;
    summary: {
      works_created: number;
      editions_created: number;
      library_entries_created: number;
      collections_created: number;
      tags_created: number;
      smart_shelves_created: number;
      reading_states_applied: number;
      reading_states_skipped: number;
    };
  };
  bytes: ByteApplyResult[];
  source_omissions: BundleOmission[];
  content_complete: boolean;
};

type ApiErrorBody = {
  detail?: string | { code?: string; detail?: string };
};

type ExportSummary = {
  included: number;
  omitted: number;
};

function byteSize(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function responseCount(response: Response, header: string): number | null {
  const raw = response.headers.get(header);
  if (raw === null) return null;
  const value = Number.parseInt(raw, 10);
  return Number.isSafeInteger(value) && value >= 0 ? value : null;
}

async function responseError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (typeof body.detail === "string") return body.detail;
    if (body.detail?.detail) {
      return body.detail.code ? `${body.detail.code}: ${body.detail.detail}` : body.detail.detail;
    }
  } catch {
    // The fallback below is intentionally used for non-JSON error responses.
  }
  return `${fallback} HTTP ${response.status}.`;
}

export function LibraryPortabilityClient() {
  const [file, setFile] = useState<File | null>(null);
  const [plan, setPlan] = useState<BundlePlan | null>(null);
  const [result, setResult] = useState<BundleApplyResult | null>(null);
  const [exportSummary, setExportSummary] = useState<ExportSummary | null>(null);
  const [busy, setBusy] = useState<"export" | "plan" | "apply" | null>(null);
  const [error, setError] = useState<string | null>(null);

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setFile(event.target.files?.[0] ?? null);
    setPlan(null);
    setResult(null);
    setError(null);
  }

  async function exportLibrary() {
    if (busy !== null) return;
    setBusy("export");
    setError(null);
    setExportSummary(null);
    try {
      const response = await apiFetch("/v1/library/export/file", { cache: "no-store" });
      if (!response.ok) throw new Error(await responseError(response, "Backup failed with"));
      const included = responseCount(response, "X-Bukmatika-Bytes-Included");
      const omitted = responseCount(response, "X-Bukmatika-Bytes-Omitted");
      if (included !== null && omitted !== null) {
        setExportSummary({ included, omitted });
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      try {
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = `bukmatika-backup-${new Date().toISOString().slice(0, 10)}.bukmatika`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Backup failed.");
    } finally {
      setBusy(null);
    }
  }

  async function planImport() {
    if (file === null || busy !== null) return;
    setBusy("plan");
    setError(null);
    setResult(null);
    try {
      const response = await apiFetch("/v1/library/import/file/plan", {
        method: "POST",
        headers: { "Content-Type": BUNDLE_MEDIA_TYPE },
        body: file,
      });
      if (!response.ok) throw new Error(await responseError(response, "Backup review failed with"));
      setPlan((await response.json()) as BundlePlan);
    } catch (caught) {
      setPlan(null);
      setError(caught instanceof Error ? caught.message : "Backup review failed.");
    } finally {
      setBusy(null);
    }
  }

  async function applyImport() {
    if (file === null || plan?.plan.can_apply !== true || busy !== null) return;
    setBusy("apply");
    setError(null);
    try {
      const response = await apiFetch("/v1/library/import/file/apply", {
        method: "POST",
        headers: { "Content-Type": BUNDLE_MEDIA_TYPE },
        body: file,
      });
      const body = (await response.json()) as BundleApplyResult | ApiErrorBody;
      if (!response.ok && !("manifest" in body)) {
        throw new Error(
          typeof body.detail === "object" && body.detail?.detail
            ? body.detail.detail
            : `Restore failed with HTTP ${response.status}.`,
        );
      }
      if (!("manifest" in body)) throw new Error("Restore returned an invalid response.");
      setResult(body);
      if (!body.manifest.committed) {
        setError("The destination changed after review. Nothing from the backup was restored.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Restore failed.");
    } finally {
      setBusy(null);
    }
  }

  const successfulBytes = result?.bytes.filter(
    (item) => item.status === "stored" || item.status === "idempotent",
  ).length ?? 0;
  const failedBytes = result?.bytes.length ? result.bytes.length - successfulBytes : 0;

  return (
    <section className={styles.surface}>
      <div className={styles.hero}>
        <div>
          <p className="eyebrow">Backup &amp; restore</p>
          <h1>Back up or restore your library.</h1>
          <p>
            A Bukmatika backup carries your catalog, reading state, and organization, plus only
            the book bytes the current source-side rights decision allows to leave this installation.
            Restore checks destination retention rights again before accepting any packaged bytes.
          </p>
        </div>
        <a className="secondary-action" href="/library">Back to library</a>
      </div>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      <div className={styles.grid}>
        <article className={styles.card}>
          <span className={styles.step}>01</span>
          <h2>Create a backup</h2>
          <p>
            Create one bounded <code>.bukmatika</code> backup. Assets that cannot be exported are
            recorded as omissions inside the package instead of being copied silently.
          </p>
          <button
            className="primary-action"
            type="button"
            disabled={busy !== null}
            onClick={() => void exportLibrary()}
          >
            {busy === "export" ? "Building backup…" : "Download backup"}
          </button>
          {exportSummary ? (
            <p className={styles.fileMeta} role="status">
              {exportSummary.included} byte payload{exportSummary.included === 1 ? "" : "s"} included ·{" "}
              {exportSummary.omitted} omitted by current export policy.
              {exportSummary.omitted > 0
                ? " Omission reasons travel inside the backup and are shown during restore review."
                : " No byte omissions were recorded."}
            </p>
          ) : null}
        </article>

        <article className={styles.card}>
          <span className={styles.step}>02</span>
          <h2>Review a backup</h2>
          <p>
            Select a Bukmatika backup. Review is dry-run only: no library records or book bytes
            change until you explicitly restore the reviewed file.
          </p>
          <label className={styles.filePicker}>
            <span>Backup file</span>
            <input type="file" accept=".bukmatika,application/zip" onChange={chooseFile} />
          </label>
          {file ? (
            <p className={styles.fileMeta}>{file.name} · {byteSize(file.size)}</p>
          ) : null}
          <button
            className="secondary-action"
            type="button"
            disabled={file === null || busy !== null}
            onClick={() => void planImport()}
          >
            {busy === "plan" ? "Reviewing…" : "Review backup"}
          </button>
        </article>
      </div>

      {plan ? (
        <section className={styles.review} aria-live="polite">
          <div className={styles.reviewHeader}>
            <div>
              <p className="eyebrow">Dry-run result</p>
              <h2>{plan.plan.can_apply ? "Ready to restore" : "Conflicts need attention"}</h2>
            </div>
            <div className={styles.metrics}>
              <span><strong>{plan.plan.entries.length}</strong> library entries</span>
              <span><strong>{plan.included_bytes.length}</strong> byte payloads</span>
              <span><strong>{plan.omitted_bytes.length}</strong> source omissions</span>
              <span><strong>{plan.plan.conflicts.length}</strong> conflicts</span>
            </div>
          </div>

          {plan.plan.conflicts.length > 0 ? (
            <div className={styles.listBlock}>
              <h3>Blocking conflicts</h3>
              <ul>
                {plan.plan.conflicts.map((conflict) => (
                  <li key={`${conflict.target}:${conflict.source_id}:${conflict.code}`}>
                    <strong>{conflict.code}</strong>
                    <span>{conflict.detail}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {plan.omitted_bytes.length > 0 ? (
            <div className={styles.listBlock}>
              <h3>Bytes intentionally omitted at the source</h3>
              <ul>
                {plan.omitted_bytes.map((omission) => (
                  <li key={`${omission.source_asset_id}:${omission.code}`}>
                    <strong>{omission.code}</strong>
                    <span>{omission.detail}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className={styles.applyRow}>
            <div>
              <strong>Restore only after reviewing the dry-run.</strong>
              <p>
                Destination-local retention policy is checked again for every included byte at
                restore time. Source rights snapshots never become destination permissions.
              </p>
            </div>
            <button
              className="primary-action"
              type="button"
              disabled={!plan.plan.can_apply || busy !== null}
              onClick={() => void applyImport()}
            >
              {busy === "apply" ? "Restoring…" : "Restore reviewed backup"}
            </button>
          </div>
        </section>
      ) : null}

      {result ? (
        <section className={styles.result} aria-live="polite">
          <p className="eyebrow">Restore result</p>
          <h2>{result.manifest.committed ? "Backup restored" : "Backup not restored"}</h2>
          <div className={styles.metrics}>
            <span><strong>{result.manifest.summary.works_created}</strong> works created</span>
            <span><strong>{result.manifest.summary.editions_created}</strong> editions created</span>
            <span><strong>{result.manifest.summary.library_entries_created}</strong> entries created</span>
            <span><strong>{successfulBytes}</strong> bytes accepted</span>
            <span><strong>{failedBytes}</strong> bytes blocked</span>
          </div>
          <p className={styles.completion}>
            {result.content_complete
              ? "All packaged content was accepted through the destination authority."
              : "The restore is intentionally partial. Review source omissions or destination byte blocks below."}
          </p>
          {result.bytes.some((item) => item.status !== "stored" && item.status !== "idempotent") ? (
            <div className={styles.listBlock}>
              <h3>Destination byte blocks</h3>
              <ul>
                {result.bytes
                  .filter((item) => item.status !== "stored" && item.status !== "idempotent")
                  .map((item) => (
                    <li key={`${item.source_asset_id}:${item.status}`}>
                      <strong>{item.code ?? item.status}</strong>
                      <span>{item.detail ?? "The destination did not accept this byte payload."}</span>
                    </li>
                  ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}
    </section>
  );
}

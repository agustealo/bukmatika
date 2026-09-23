"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";

type AggregateStatus = "ready" | "in_progress" | "needs_action" | "failed";
type StatusFilter = "all" | AggregateStatus;
type AssetStage = "rights" | "acquisition" | "processing" | "ocr" | "ready";

type LibraryStatusSummary = {
  total: number;
  ready: number;
  in_progress: number;
  needs_action: number;
  failed: number;
};

type LibraryAssetStatusItem = {
  library_entry_id: string;
  work_id: string;
  work_title: string;
  edition_id: string;
  edition_title: string;
  asset_id: string;
  format: string;
  media_type: string | null;
  aggregate_status: AggregateStatus;
  stage: AssetStage;
  status_code: string;
  rights_state: string | null;
  acquisition_allowed: boolean | null;
  acquisition_id: string | null;
  acquisition_status: string | null;
  acquisition_job_id: string | null;
  acquisition_job_status: string | null;
  acquisition_error_code: string | null;
  processing_status: string | null;
  processing_error_code: string | null;
  ocr_job_id: string | null;
  ocr_job_status: string | null;
  ocr_error_code: string | null;
  document_id: string | null;
};

type LibraryStatusResponse = {
  summary: LibraryStatusSummary;
  items: LibraryAssetStatusItem[];
};

const EMPTY_STATUS: LibraryStatusResponse = {
  summary: { total: 0, ready: 0, in_progress: 0, needs_action: 0, failed: 0 },
  items: [],
};

const STATUS_LABELS: Record<AggregateStatus, string> = {
  ready: "Ready",
  in_progress: "In progress",
  needs_action: "Needs attention",
  failed: "Failed",
};

const STATUS_MESSAGES: Record<string, string> = {
  DOCUMENT_READY: "A canonical processed document is ready to read.",
  OCR_IN_PROGRESS: "OCR is running through the durable job queue.",
  OCR_COMPLETED_DOCUMENT_MISSING: "OCR completed but no canonical document was committed.",
  OCR_CANCELLED: "OCR was cancelled and can be reconsidered from the dossier.",
  OCR_REQUIRED: "This stored PDF needs OCR before it can become readable.",
  PROCESSING_IN_PROGRESS: "Document processing is currently running.",
  PROCESSING_COMPLETED_DOCUMENT_MISSING:
    "Processing reports complete but the canonical document is missing.",
  PROCESSING_FAILED: "Document processing failed.",
  ACQUISITION_FAILED: "Acquisition failed before the asset could be stored.",
  ACQUISITION_QUARANTINED: "The downloaded asset is quarantined for review.",
  ACQUISITION_IN_PROGRESS: "Acquisition is queued or actively downloading and verifying.",
  ACQUISITION_CANCELLED: "Acquisition was cancelled.",
  PROCESSING_REQUIRED: "The verified asset is stored and ready for document processing.",
  ACQUISITION_READY: "Rights permit acquisition when you choose this asset.",
  RIGHTS_REVIEW_REQUIRED: "Rights evidence is not strong enough to permit acquisition yet.",
  RIGHTS_BLOCKED: "Current rights policy does not permit automated acquisition.",
};

function humanizeCode(code: string): string {
  return STATUS_MESSAGES[code] ?? code.replaceAll("_", " ").toLowerCase();
}

function detailValue(value: string | null): string {
  return value ?? "not started";
}

function summaryCount(summary: LibraryStatusSummary, filter: StatusFilter): number {
  if (filter === "all") return summary.total;
  return summary[filter];
}

export function LibraryStatusClient() {
  const [status, setStatus] = useState<LibraryStatusResponse>(EMPTY_STATUS);
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    const response = await apiFetch("/v1/library/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`Library status failed with HTTP ${response.status}.`);
    setStatus((await response.json()) as LibraryStatusResponse);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch("/v1/library/status", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Library status failed with HTTP ${response.status}.`);
        }
        const body = (await response.json()) as LibraryStatusResponse;
        if (!cancelled) setStatus(body);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Library status failed.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  const visibleItems = useMemo(
    () =>
      filter === "all"
        ? status.items
        : status.items.filter((item) => item.aggregate_status === filter),
    [filter, status.items],
  );

  async function refresh() {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      await loadStatus();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Library status refresh failed.");
    } finally {
      setRefreshing(false);
    }
  }

  if (loading) {
    return <div className="surface-loading" aria-busy="true">Reading the library pipeline…</div>;
  }

  return (
    <section className="status-surface" aria-label="Library processing status">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Library pipeline</p>
          <h1>What is ready, moving, blocked, or broken.</h1>
          <p>
            This is a live read of canonical rights, acquisition, processing, OCR, and document
            state for books in your library. It does not maintain a second queue or status table.
          </p>
        </div>
        <button className="secondary-action" type="button" disabled={refreshing} onClick={() => void refresh()}>
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      <div className="status-summary" role="group" aria-label="Filter pipeline status">
        {(
          [
            ["all", "All"],
            ["failed", "Failed"],
            ["needs_action", "Needs attention"],
            ["in_progress", "In progress"],
            ["ready", "Ready"],
          ] as const
        ).map(([value, label]) => (
          <button
            type="button"
            className={filter === value ? "is-active" : ""}
            aria-pressed={filter === value}
            key={value}
            onClick={() => setFilter(value)}
          >
            <strong>{summaryCount(status.summary, value)}</strong>
            <span>{label}</span>
          </button>
        ))}
      </div>

      {visibleItems.length === 0 ? (
        <div className="empty-surface">
          <strong>{status.summary.total === 0 ? "No tracked library assets yet." : "Nothing in this bucket."}</strong>
          <p>
            {status.summary.total === 0
              ? "Save a work or edition to your library. Its canonical asset pipeline will appear here."
              : "Choose another status filter to inspect the rest of the pipeline."}
          </p>
          {status.summary.total === 0 ? (
            <a className="primary-action link-button" href="/">Discover books</a>
          ) : null}
        </div>
      ) : (
        <div className="status-list" aria-live="polite">
          {visibleItems.map((item) => (
            <article className="status-card" key={item.asset_id}>
              <div className="status-card-heading">
                <div>
                  <div className="status-card-topline">
                    <span className={`status-pill status-${item.aggregate_status}`}>
                      {STATUS_LABELS[item.aggregate_status]}
                    </span>
                    <span>{item.format}</span>
                    <span>{item.stage}</span>
                  </div>
                  <h2>{item.work_title}</h2>
                  <p>{item.edition_title}</p>
                </div>
                <div className="status-card-actions">
                  {item.document_id ? (
                    <a
                      className="primary-action link-button"
                      href={`/read/${item.library_entry_id}/${item.document_id}`}
                    >
                      Read
                    </a>
                  ) : null}
                  <a className="secondary-link" href={`/dossier?work_id=${item.work_id}`}>Details</a>
                </div>
              </div>

              <div className="status-reason">
                <strong>{humanizeCode(item.status_code)}</strong>
                <code>{item.status_code}</code>
              </div>

              <dl className="status-details">
                <div>
                  <dt>Rights</dt>
                  <dd>{detailValue(item.rights_state)}</dd>
                </div>
                <div>
                  <dt>Acquisition</dt>
                  <dd>{detailValue(item.acquisition_status ?? item.acquisition_job_status)}</dd>
                </div>
                <div>
                  <dt>Processing</dt>
                  <dd>{detailValue(item.processing_status)}</dd>
                </div>
                <div>
                  <dt>OCR</dt>
                  <dd>{detailValue(item.ocr_job_status)}</dd>
                </div>
              </dl>

              {item.acquisition_error_code || item.processing_error_code || item.ocr_error_code ? (
                <div className="status-errors" aria-label="Pipeline error codes">
                  {item.acquisition_error_code ? <code>{item.acquisition_error_code}</code> : null}
                  {item.processing_error_code ? <code>{item.processing_error_code}</code> : null}
                  {item.ocr_error_code ? <code>{item.ocr_error_code}</code> : null}
                </div>
              ) : null}
            </article>
          ))}
        </div>
      )}

      <p className="status-safety-note">
        Pipeline actions stay in their canonical product surfaces. This view does not cancel or retry
        asset-global jobs on behalf of one library user.
      </p>
    </section>
  );
}

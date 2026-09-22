"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";

type AssetStatus = {
  asset_id: string;
  format: string;
  media_type: string | null;
  byte_size: number | null;
  stored: boolean;
  acquisition_id: string | null;
  acquisition_status: string | null;
  processing_status: string | null;
  processing_error_code: string | null;
  ocr_job_id: string | null;
  ocr_job_status: string | null;
  document_id: string | null;
  rights_state: string | null;
  acquisition_allowed: boolean | null;
};

type EditionDossier = {
  edition_id: string;
  title: string;
  language: string | null;
  publication_year: number | null;
  publisher: string | null;
  edition_statement: string | null;
  library_entry_id: string | null;
  assets: AssetStatus[];
};

type WorkDossier = {
  work_id: string;
  title: string;
  authors: string[];
  subjects: string[];
  work_library_entry_id: string | null;
  editions: EditionDossier[];
};

type DossierClientProps =
  | { provider: string; recordId: string; workId?: never }
  | { workId: string; provider?: never; recordId?: never };

const ACTIVE_JOB_STATES = new Set(["queued", "running", "resolving", "downloading", "verifying"]);

function pretty(value: string | null): string {
  if (!value) return "Not available";
  return value.replaceAll("_", " ");
}

function bytesLabel(bytes: number | null): string | null {
  if (bytes === null) return null;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function DossierClient(props: DossierClientProps) {
  const [dossier, setDossier] = useState<WorkDossier | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);

  const dossierPath = useMemo(() => {
    if (props.workId) return `/v1/dossiers/works/${props.workId}`;
    const params = new URLSearchParams({ provider: props.provider, record_id: props.recordId });
    return `/v1/dossiers/source?${params.toString()}`;
  }, [props]);

  const load = useCallback(async () => {
    const response = await apiFetch(dossierPath, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Dossier failed with HTTP ${response.status}.`);
    }
    setDossier((await response.json()) as WorkDossier);
  }, [dossierPath]);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch(dossierPath, { cache: "no-store" });
        if (!response.ok) throw new Error(`Dossier failed with HTTP ${response.status}.`);
        const body = (await response.json()) as WorkDossier;
        if (!cancelled) setDossier(body);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Dossier failed.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [dossierPath]);

  async function perform(key: string, path: string) {
    if (actionKey) return;
    setActionKey(key);
    setError(null);
    try {
      const response = await apiFetch(path, { method: "POST" });
      if (!response.ok) {
        throw new Error(`Action failed with HTTP ${response.status}.`);
      }
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action failed.");
    } finally {
      setActionKey(null);
    }
  }

  if (loading) {
    return <div className="surface-loading" aria-busy="true">Resolving canonical work and editions…</div>;
  }
  if (error && !dossier) {
    return <div className="error-card" role="alert">{error}</div>;
  }
  if (!dossier) return null;

  const anyEditionSaved = dossier.editions.some((edition) => edition.library_entry_id !== null);

  return (
    <div className="dossier-wrap">
      {error ? <div className="error-card" role="alert">{error}</div> : null}

      <section className="dossier-hero">
        <div>
          <p className="eyebrow">Canonical work dossier</p>
          <h1>{dossier.title}</h1>
          <p className="dossier-byline">
            {dossier.authors.length > 0 ? dossier.authors.join(", ") : "Unknown author"}
          </p>
          {dossier.subjects.length > 0 ? (
            <p className="dossier-subjects">{dossier.subjects.slice(0, 10).join(" · ")}</p>
          ) : null}
        </div>
        {!dossier.work_library_entry_id && !anyEditionSaved ? (
          <button
            className="primary-action"
            type="button"
            disabled={actionKey !== null}
            onClick={() => void perform(`work:${dossier.work_id}`, `/v1/library/works/${dossier.work_id}`)}
          >
            {actionKey === `work:${dossier.work_id}` ? "Saving…" : "Save work"}
          </button>
        ) : (
          <a className="primary-action link-button" href="/library">Open library</a>
        )}
      </section>

      <section className="edition-list" aria-label="Available editions">
        {dossier.editions.length === 0 ? (
          <div className="empty-surface">
            <strong>No resolved editions yet.</strong>
            <p>This source currently resolves to a work record without a concrete digital edition.</p>
          </div>
        ) : null}

        {dossier.editions.map((edition) => (
          <article className="edition-card" key={edition.edition_id}>
            <div className="edition-heading">
              <div>
                <p className="source-label">Edition</p>
                <h2>{edition.title}</h2>
                <div className="edition-facts">
                  {edition.publication_year ? <span>{edition.publication_year}</span> : null}
                  {edition.language ? <span>{edition.language.toUpperCase()}</span> : null}
                  {edition.publisher ? <span>{edition.publisher}</span> : null}
                </div>
              </div>
              {edition.library_entry_id ? (
                <span className="saved-chip">Saved</span>
              ) : (
                <button
                  className="secondary-action"
                  type="button"
                  disabled={actionKey !== null}
                  onClick={() => void perform(`edition:${edition.edition_id}`, `/v1/library/editions/${edition.edition_id}`)}
                >
                  {actionKey === `edition:${edition.edition_id}` ? "Saving…" : "Save edition"}
                </button>
              )}
            </div>

            <div className="asset-list">
              {edition.assets.length === 0 ? (
                <p className="muted-copy">No concrete digital assets are resolved for this edition yet.</p>
              ) : null}
              {edition.assets.map((asset) => {
                const acquisitionBusy = asset.acquisition_status
                  ? ACTIVE_JOB_STATES.has(asset.acquisition_status)
                  : false;
                const ocrBusy = asset.ocr_job_status ? ACTIVE_JOB_STATES.has(asset.ocr_job_status) : false;
                const size = bytesLabel(asset.byte_size);
                return (
                  <div className="asset-row" key={asset.asset_id}>
                    <div className="asset-summary">
                      <strong>{asset.format}</strong>
                      {size ? <span>{size}</span> : null}
                      <span className={`rights-chip rights-${asset.rights_state ?? "unknown"}`}>
                        {pretty(asset.rights_state)}
                      </span>
                    </div>
                    <div className="asset-state" aria-label={`${asset.format} status`}>
                      <span>Acquisition: {asset.stored ? "stored" : pretty(asset.acquisition_status)}</span>
                      <span>Processing: {pretty(asset.processing_status)}</span>
                      {asset.ocr_job_status ? <span>OCR: {pretty(asset.ocr_job_status)}</span> : null}
                      {asset.processing_error_code ? <span>Error: {asset.processing_error_code}</span> : null}
                    </div>
                    <div className="asset-actions">
                      {asset.document_id && edition.library_entry_id ? (
                        <a
                          className="primary-action link-button"
                          href={`/read/${edition.library_entry_id}/${asset.document_id}`}
                        >
                          Read
                        </a>
                      ) : null}
                      {!asset.stored && asset.acquisition_allowed === true && !acquisitionBusy ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() => void perform(`acquire:${asset.asset_id}`, `/v1/assets/${asset.asset_id}/acquire`)}
                        >
                          {actionKey === `acquire:${asset.asset_id}` ? "Queuing…" : "Acquire"}
                        </button>
                      ) : null}
                      {asset.stored && !asset.document_id && asset.processing_status !== "requires_ocr" ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() => void perform(`process:${asset.asset_id}`, `/v1/assets/${asset.asset_id}/process`)}
                        >
                          {actionKey === `process:${asset.asset_id}` ? "Processing…" : "Process"}
                        </button>
                      ) : null}
                      {asset.processing_status === "requires_ocr" && !ocrBusy ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() => void perform(`ocr:${asset.asset_id}`, `/v1/assets/${asset.asset_id}/ocr`)}
                        >
                          {actionKey === `ocr:${asset.asset_id}` ? "Queuing OCR…" : "Run OCR"}
                        </button>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}

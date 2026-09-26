"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import { CitationExport } from "./citation-export";

type AcquisitionApprovalMode = "always_ask" | "auto_eligible";

type AcquisitionPolicy = {
  approval_mode: AcquisitionApprovalMode;
};

type AssetStatus = {
  asset_id: string;
  format: string;
  media_type: string | null;
  byte_size: number | null;
  stored: boolean;
  acquisition_id: string | null;
  acquisition_status: string | null;
  acquisition_request_id: string | null;
  acquisition_request_status: string | null;
  acquisition_approval_mode: string | null;
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
  | { provider: string; recordId: string }
  | { workId: string };

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
  const [acquisitionPolicy, setAcquisitionPolicy] = useState<AcquisitionPolicy | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionKey, setActionKey] = useState<string | null>(null);

  const dossierPath = useMemo(() => {
    if ("workId" in props) return `/v1/dossiers/works/${props.workId}`;
    const params = new URLSearchParams({ provider: props.provider, record_id: props.recordId });
    return `/v1/dossiers/source?${params.toString()}`;
  }, [props]);

  const load = useCallback(async () => {
    const [dossierResponse, policyResponse] = await Promise.all([
      apiFetch(dossierPath, { cache: "no-store" }),
      apiFetch("/v1/acquisition-policy", { cache: "no-store" }),
    ]);
    if (!dossierResponse.ok) {
      throw new Error(`Dossier failed with HTTP ${dossierResponse.status}.`);
    }
    if (!policyResponse.ok) {
      throw new Error(`Acquisition policy failed with HTTP ${policyResponse.status}.`);
    }
    const [dossierBody, policyBody] = await Promise.all([
      dossierResponse.json() as Promise<WorkDossier>,
      policyResponse.json() as Promise<AcquisitionPolicy>,
    ]);
    setDossier(dossierBody);
    setAcquisitionPolicy(policyBody);
  }, [dossierPath]);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const [dossierResponse, policyResponse] = await Promise.all([
          apiFetch(dossierPath, { cache: "no-store" }),
          apiFetch("/v1/acquisition-policy", { cache: "no-store" }),
        ]);
        if (!dossierResponse.ok) {
          throw new Error(`Dossier failed with HTTP ${dossierResponse.status}.`);
        }
        if (!policyResponse.ok) {
          throw new Error(`Acquisition policy failed with HTTP ${policyResponse.status}.`);
        }
        const [dossierBody, policyBody] = await Promise.all([
          dossierResponse.json() as Promise<WorkDossier>,
          policyResponse.json() as Promise<AcquisitionPolicy>,
        ]);
        if (!cancelled) {
          setDossier(dossierBody);
          setAcquisitionPolicy(policyBody);
        }
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

  async function updatePolicy(approvalMode: AcquisitionApprovalMode) {
    if (actionKey) return;
    setActionKey("acquisition-policy");
    setError(null);
    try {
      const response = await apiFetch("/v1/acquisition-policy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approval_mode: approvalMode }),
      });
      if (!response.ok) {
        throw new Error(`Acquisition policy update failed with HTTP ${response.status}.`);
      }
      setAcquisitionPolicy((await response.json()) as AcquisitionPolicy);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Acquisition policy update failed.");
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

      {acquisitionPolicy ? (
        <section className="dossier-hero" aria-labelledby="acquisition-policy-title">
          <div>
            <p className="eyebrow">Acquisition approval</p>
            <h2 id="acquisition-policy-title">How explicit requests start</h2>
            <p className="muted-copy">
              Rights checks always run before download. This setting only controls whether an eligible
              request needs a second approval click.
            </p>
          </div>
          <label>
            <span className="source-label">Approval policy</span>
            <select
              value={acquisitionPolicy.approval_mode}
              disabled={actionKey !== null}
              onChange={(event) =>
                void updatePolicy(event.target.value as AcquisitionApprovalMode)
              }
            >
              <option value="always_ask">Ask before download</option>
              <option value="auto_eligible">Start eligible requests immediately</option>
            </select>
          </label>
        </section>
      ) : null}

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

            <CitationExport
              workId={dossier.work_id}
              editionId={edition.edition_id}
              editionTitle={edition.title}
            />

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
                const pendingApproval = asset.acquisition_request_status === "pending_approval";
                const requestActive = asset.acquisition_request_status === "active";
                const requestCanRestart = asset.acquisition_request_status === "cancelled";
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
                      {asset.acquisition_request_status ? (
                        <span>Your request: {pretty(asset.acquisition_request_status)}</span>
                      ) : null}
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
                      {!asset.stored &&
                      asset.acquisition_allowed === true &&
                      asset.acquisition_request_id === null &&
                      !acquisitionBusy ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() => void perform(`acquire:${asset.asset_id}`, `/v1/assets/${asset.asset_id}/acquire`)}
                        >
                          {actionKey === `acquire:${asset.asset_id}` ? "Requesting…" : "Request acquisition"}
                        </button>
                      ) : null}
                      {!asset.stored && asset.acquisition_allowed === true && requestCanRestart ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() => void perform(`acquire:${asset.asset_id}`, `/v1/assets/${asset.asset_id}/acquire`)}
                        >
                          {actionKey === `acquire:${asset.asset_id}` ? "Requesting…" : "Request again"}
                        </button>
                      ) : null}
                      {pendingApproval && asset.acquisition_request_id ? (
                        <button
                          className="primary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() =>
                            void perform(
                              `approve:${asset.acquisition_request_id}`,
                              `/v1/acquisition-requests/${asset.acquisition_request_id}/approve`,
                            )
                          }
                        >
                          {actionKey === `approve:${asset.acquisition_request_id}` ? "Approving…" : "Approve download"}
                        </button>
                      ) : null}
                      {(pendingApproval || requestActive) && asset.acquisition_request_id ? (
                        <button
                          className="secondary-action"
                          type="button"
                          disabled={actionKey !== null}
                          onClick={() =>
                            void perform(
                              `cancel:${asset.acquisition_request_id}`,
                              `/v1/acquisition-requests/${asset.acquisition_request_id}/cancel`,
                            )
                          }
                        >
                          {actionKey === `cancel:${asset.acquisition_request_id}` ? "Cancelling…" : "Cancel my request"}
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

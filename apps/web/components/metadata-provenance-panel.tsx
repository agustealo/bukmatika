"use client";

import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./metadata-provenance-panel.module.css";

type MetadataAssertion = {
  field_name: string;
  value: unknown;
  provider: string;
  provider_record_id: string;
  source_url: string;
  confidence: number;
  normalization_method: string | null;
  parser_version: string;
  first_observed_at: string;
  last_observed_at: string;
  observation_count: number;
  assertion_created_at: string;
};

type EditionMetadataProvenance = {
  edition_id: string;
  title: string;
  assertions: MetadataAssertion[];
};

type MetadataProvenance = {
  work_id: string;
  title: string;
  assertions: MetadataAssertion[];
  editions: EditionMetadataProvenance[];
};

type MetadataProvenancePanelProps = {
  workId?: string;
  provider?: string;
  recordId?: string;
};

function endpoint({ workId, provider, recordId }: MetadataProvenancePanelProps): string | null {
  if (workId) {
    return `/v1/dossiers/works/${encodeURIComponent(workId)}/metadata-provenance`;
  }
  if (provider && recordId) {
    const params = new URLSearchParams({ provider, record_id: recordId });
    return `/v1/dossiers/source/metadata-provenance?${params.toString()}`;
  }
  return null;
}

function displayValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map((item) => displayValue(item)).join(", ");
  if (value === null || value === undefined) return "Unknown";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function fieldLabel(fieldName: string): string {
  return fieldName
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function observedLabel(assertion: MetadataAssertion): string {
  const first = new Date(assertion.first_observed_at);
  const last = new Date(assertion.last_observed_at);
  if (Number.isNaN(first.getTime()) || Number.isNaN(last.getTime())) {
    return `${assertion.observation_count} observation${assertion.observation_count === 1 ? "" : "s"}`;
  }
  const count = `${assertion.observation_count} observation${assertion.observation_count === 1 ? "" : "s"}`;
  if (first.toDateString() === last.toDateString()) {
    return `${count} · ${last.toLocaleDateString()}`;
  }
  return `${count} · ${first.toLocaleDateString()} to ${last.toLocaleDateString()}`;
}

function AssertionList({ assertions }: { assertions: MetadataAssertion[] }) {
  if (assertions.length === 0) {
    return <p className={styles.empty}>No field-level assertions have been recorded yet.</p>;
  }

  return (
    <div className={styles.assertions}>
      {assertions.map((assertion, index) => (
        <article
          className={styles.assertion}
          key={`${assertion.field_name}:${assertion.provider}:${assertion.provider_record_id}:${index}`}
        >
          <div className={styles.assertionHeading}>
            <strong>{fieldLabel(assertion.field_name)}</strong>
            <span>{Math.round(assertion.confidence * 100)}% confidence</span>
          </div>
          <p className={styles.value}>{displayValue(assertion.value)}</p>
          <dl className={styles.meta}>
            <div>
              <dt>Source</dt>
              <dd>
                <a href={assertion.source_url} target="_blank" rel="noreferrer">
                  {assertion.provider}
                </a>
                <span>{assertion.provider_record_id}</span>
              </dd>
            </div>
            <div>
              <dt>Observed</dt>
              <dd>{observedLabel(assertion)}</dd>
            </div>
            <div>
              <dt>Normalization</dt>
              <dd>{assertion.normalization_method ?? "source value"}</dd>
            </div>
            <div>
              <dt>Parser</dt>
              <dd>{assertion.parser_version}</dd>
            </div>
          </dl>
        </article>
      ))}
    </div>
  );
}

export function MetadataProvenancePanel(props: MetadataProvenancePanelProps) {
  const requestPath = useMemo(
    () => endpoint(props),
    [props.provider, props.recordId, props.workId],
  );
  const [provenance, setProvenance] = useState<MetadataProvenance | null>(null);
  const [loading, setLoading] = useState(requestPath !== null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (requestPath === null) {
      setLoading(false);
      setProvenance(null);
      return () => {
        cancelled = true;
      };
    }

    async function load() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch(requestPath, { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`Metadata provenance failed with HTTP ${response.status}.`);
        }
        const payload = (await response.json()) as MetadataProvenance;
        if (!cancelled) setProvenance(payload);
      } catch (caught) {
        if (!cancelled) {
          setProvenance(null);
          setError(
            caught instanceof Error ? caught.message : "Metadata provenance is unavailable.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [requestPath]);

  if (requestPath === null) return null;

  const assertionCount = provenance
    ? provenance.assertions.length +
      provenance.editions.reduce((total, edition) => total + edition.assertions.length, 0)
    : 0;

  return (
    <section className={styles.panel} aria-labelledby="metadata-provenance-heading">
      <div className={styles.heading}>
        <div>
          <span>Catalog evidence</span>
          <h2 id="metadata-provenance-heading">Metadata provenance</h2>
        </div>
        {provenance ? <strong>{assertionCount} assertions</strong> : null}
      </div>
      <p className={styles.intro}>
        Inspect the exact provider observations behind catalog fields. Conflicting source claims stay
        visible here instead of being silently discarded.
      </p>

      {loading ? <p className={styles.empty}>Loading metadata provenance…</p> : null}
      {error ? <p className={styles.error} role="alert">{error}</p> : null}

      {provenance ? (
        <div className={styles.groups}>
          <details className={styles.group} open>
            <summary>
              <span>Work metadata</span>
              <strong>{provenance.assertions.length}</strong>
            </summary>
            <AssertionList assertions={provenance.assertions} />
          </details>

          {provenance.editions.map((edition) => (
            <details className={styles.group} key={edition.edition_id}>
              <summary>
                <span>{edition.title}</span>
                <strong>{edition.assertions.length}</strong>
              </summary>
              <AssertionList assertions={edition.assertions} />
            </details>
          ))}
        </div>
      ) : null}
    </section>
  );
}

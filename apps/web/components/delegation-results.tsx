"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./delegation-results.module.css";

type ResearchPassage = {
  library_entry_id: string;
  work_title: string;
  edition_title: string;
  document_id: string;
  section_id: string;
  section_ordinal: number;
  char_start: number;
  char_end: number;
  text: string;
  score: number;
};

type DelegationResultSource = {
  library_entry_id: string;
  work_title: string | null;
  edition_title: string | null;
  available: boolean;
};

type DelegationOutcomeStatus = "rejected" | "stopped" | "completed" | "failed" | "cancelled";

type DelegationResult = {
  delegation_id: string;
  attempt_id: string | null;
  step_id: string | null;
  capability: string | null;
  status: DelegationOutcomeStatus;
  outcome_at: string;
  completed_at: string | null;
  failure_code: string | null;
  attempt_error_code: string | null;
  user_request: string | null;
  available: boolean;
  unavailable_reason: string | null;
  query: string | null;
  selected_library_entry_ids: string[];
  selected_sources: DelegationResultSource[];
  passages: ResearchPassage[];
};

const REFRESH_INTERVAL_MS = 5_000;

function sourceHref(passage: ResearchPassage): string {
  const section = encodeURIComponent(String(passage.section_ordinal));
  const offset = encodeURIComponent(String(passage.char_start));
  return (
    `/library/${passage.library_entry_id}/read/${passage.document_id}` +
    `?section=${section}&offset=${offset}#reader-section-${passage.section_id}`
  );
}

function statusLabel(status: DelegationOutcomeStatus): string {
  switch (status) {
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "stopped":
      return "Stopped";
    case "cancelled":
      return "Cancelled";
    case "rejected":
      return "Rejected";
  }
}

function terminalMessage(result: DelegationResult): string {
  const code = result.failure_code ?? result.attempt_error_code;
  switch (result.status) {
    case "failed":
      return code
        ? `The delegated search failed (${code}).`
        : "The delegated search failed before producing a result.";
    case "stopped":
      return "The delegated search was stopped before completion.";
    case "cancelled":
      return "The delegated search was cancelled before execution completed.";
    case "rejected":
      return "The delegation proposal was rejected and was not executed.";
    case "completed":
      return result.unavailable_reason ?? "The canonical source is no longer available.";
  }
}

function SourceScope({ result }: { result: DelegationResult }) {
  if (result.selected_sources.length === 0) return null;
  return (
    <div className={styles.sourceScope}>
      <p className={styles.scopeLabel}>
        Selected books · {result.selected_sources.length}
      </p>
      <ul className={styles.sourceList}>
        {result.selected_sources.map((source) => (
          <li className={styles.sourceChip} data-available={source.available} key={source.library_entry_id}>
            {source.available && source.work_title ? (
              <>
                <strong>{source.work_title}</strong>
                {source.edition_title ? <span>{source.edition_title}</span> : null}
              </>
            ) : (
              <span>Source no longer in library</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function DelegationResults() {
  const [results, setResults] = useState<DelegationResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await apiFetch("/v1/personalization/delegation-results");
      if (!response.ok) {
        throw new Error(`Recent outcomes failed with HTTP ${response.status}.`);
      }
      setResults((await response.json()) as DelegationResult[]);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Recent delegation outcomes are unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return (
    <section className={styles.panel} aria-labelledby="delegation-results-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>Delegated work</p>
          <h2 id="delegation-results-title">Recent delegation outcomes</h2>
          <p className={styles.intro}>
            Completed, failed, stopped, cancelled, and rejected read-only delegations stay
            inspectable here. Successful source text is loaded from your current canonical library
            rather than copied into a second AI store.
          </p>
        </div>
        <button className={styles.refreshButton} type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>

      {loading ? <p className={styles.muted}>Loading recent outcomes…</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      {!loading && !error && results.length === 0 ? (
        <p className={styles.muted}>No terminal delegated work yet.</p>
      ) : null}

      <div className={styles.results}>
        {results.map((result) => {
          const terminal = result.status !== "completed";
          const key =
            result.attempt_id ?? `${result.delegation_id}:${result.status}:${result.outcome_at}`;
          return (
            <article className={styles.resultCard} key={key}>
              <div className={styles.resultMeta}>
                <span>{result.capability ?? "read-only delegation"}</span>
                <span className={styles.statusBadge} data-status={result.status}>
                  {statusLabel(result.status)}
                </span>
                <span>{new Date(result.outcome_at).toLocaleString()}</span>
              </div>
              {terminal ? (
                <>
                  <h3>{result.user_request ?? `${statusLabel(result.status)} delegated research`}</h3>
                  {result.query ? (
                    <p className={styles.plannedQuery}>
                      <strong>Planned query:</strong> {result.query}
                    </p>
                  ) : null}
                  <SourceScope result={result} />
                  <p className={result.status === "failed" ? styles.failure : styles.unavailable}>
                    {terminalMessage(result)}
                  </p>
                </>
              ) : result.available ? (
                <>
                  <h3>{result.query ?? "Delegated research search"}</h3>
                  <SourceScope result={result} />
                  {result.passages.length === 0 ? (
                    <p className={styles.muted}>The search completed with no matching passages.</p>
                  ) : (
                    <ol className={styles.passages}>
                      {result.passages.map((passage) => (
                        <li
                          className={styles.passage}
                          key={`${result.attempt_id}:${passage.document_id}:${passage.char_start}`}
                        >
                          <div className={styles.passageHeading}>
                            <strong>{passage.work_title}</strong>
                            <span>{passage.edition_title}</span>
                          </div>
                          <p>{passage.text}</p>
                          <a className={styles.sourceLink} href={sourceHref(passage)}>
                            Open source
                          </a>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              ) : (
                <>
                  {result.query ? <h3>{result.query}</h3> : null}
                  <SourceScope result={result} />
                  <p className={styles.unavailable}>{terminalMessage(result)}</p>
                </>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

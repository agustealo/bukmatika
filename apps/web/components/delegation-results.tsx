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

type DelegationResult = {
  delegation_id: string;
  attempt_id: string;
  step_id: string;
  capability: string;
  completed_at: string;
  available: boolean;
  unavailable_reason: string | null;
  query: string | null;
  selected_library_entry_ids: string[];
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

export function DelegationResults() {
  const [results, setResults] = useState<DelegationResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const response = await apiFetch("/v1/personalization/delegation-results");
      if (!response.ok) {
        throw new Error(`Recent results failed with HTTP ${response.status}.`);
      }
      setResults((await response.json()) as DelegationResult[]);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Recent delegation results are unavailable.");
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
          <p className={styles.eyebrow}>07 · Completed work</p>
          <h2 id="delegation-results-title">Recent delegation results</h2>
          <p className={styles.intro}>
            Completed read-only searches stay inspectable here. Source text is loaded from your
            current canonical library rather than copied into a second AI store.
          </p>
        </div>
        <button className={styles.refreshButton} type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>

      {loading ? <p className={styles.muted}>Loading recent results…</p> : null}
      {error ? <p className={styles.error}>{error}</p> : null}
      {!loading && !error && results.length === 0 ? (
        <p className={styles.muted}>No completed delegated searches yet.</p>
      ) : null}

      <div className={styles.results}>
        {results.map((result) => (
          <article className={styles.resultCard} key={result.attempt_id}>
            <div className={styles.resultMeta}>
              <span>{result.capability}</span>
              <span>{new Date(result.completed_at).toLocaleString()}</span>
            </div>
            {result.available ? (
              <>
                <h3>{result.query ?? "Delegated research search"}</h3>
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
              <p className={styles.unavailable}>
                {result.unavailable_reason ?? "The canonical source is no longer available."}
              </p>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./personalized-recommendations.module.css";

type RecommendationSignal =
  | "subject"
  | "author"
  | "region"
  | "period"
  | "language"
  | "format"
  | "source";

type RecommendationReason = {
  claim_id: string;
  preference_key: string;
  source: "explicit" | "inferred";
  confidence: number;
  signal: RecommendationSignal;
  matched_values: string[];
  contribution: number;
};

type PersonalizedRecommendation = {
  work_id: string;
  title: string;
  authors: string[];
  subjects: string[];
  languages: string[];
  formats: string[];
  publication_years: number[];
  source: string;
  source_record_id: string;
  fit_score: number;
  reasons: RecommendationReason[];
};

type PersonalizedRecommendationsResponse = {
  items: PersonalizedRecommendation[];
  active_ranking_claims: number;
  supported_ranking_claims: number;
  explanation: string;
};

function dossierHref(item: PersonalizedRecommendation): string {
  const params = new URLSearchParams({
    provider: item.source,
    record_id: item.source_record_id,
  });
  return `/dossier?${params.toString()}`;
}

function signalLabel(signal: RecommendationSignal): string {
  return signal.charAt(0).toUpperCase() + signal.slice(1);
}

function sourceLabel(source: string): string {
  return source.replaceAll("_", " ");
}

export function PersonalizedRecommendations() {
  const [response, setResponse] = useState<PersonalizedRecommendationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiFetch("/v1/personalization/recommendations?limit=6", {
        cache: "no-store",
      });
      if (!result.ok) {
        throw new Error(`Recommendations failed with HTTP ${result.status}.`);
      }
      setResponse((await result.json()) as PersonalizedRecommendationsResponse);
    } catch (caught) {
      setResponse(null);
      setError(caught instanceof Error ? caught.message : "Recommendations are unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className={styles.surface} aria-labelledby="personalized-recommendations-heading">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>Personalized discovery</p>
          <h2 id="personalized-recommendations-heading">Why these books may fit you</h2>
          <p className={styles.intro}>
            This is a separate, explainable view over Bukmatika&apos;s persisted catalog. It does not
            alter live search, rights decisions, or acquisition eligibility.
          </p>
        </div>
        <button type="button" className={styles.refresh} onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error ? (
        <div className={styles.error} role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>Try again</button>
        </div>
      ) : null}

      {!error && loading && response === null ? (
        <p className={styles.muted} aria-live="polite">Loading preference-fit recommendations…</p>
      ) : null}

      {!error && !loading && response && response.items.length === 0 ? (
        <div className={styles.empty}>
          <strong>No explained recommendations yet.</strong>
          <p>{response.explanation}</p>
          <a href="/personalization">Review your preferences</a>
        </div>
      ) : null}

      {response && response.items.length > 0 ? (
        <>
          <div className={styles.disclosure}>
            <span>{response.items.length} catalog match{response.items.length === 1 ? "" : "es"}</span>
            <span>{response.supported_ranking_claims} supported ranking preference{response.supported_ranking_claims === 1 ? "" : "s"}</span>
          </div>
          <div className={styles.grid}>
            {response.items.map((item) => (
              <article className={styles.card} key={item.work_id}>
                <div className={styles.cardTopline}>
                  <span>Preference fit {item.fit_score.toFixed(2)}</span>
                  <span>{sourceLabel(item.source)}</span>
                </div>
                <h3>{item.title}</h3>
                <p className={styles.byline}>
                  {item.authors.length > 0 ? item.authors.join(", ") : "Unknown author"}
                </p>
                <div className={styles.facts}>
                  {item.publication_years.slice(0, 2).map((year) => <span key={year}>{year}</span>)}
                  {item.languages.slice(0, 2).map((language) => (
                    <span key={language}>{language.toUpperCase()}</span>
                  ))}
                  {item.formats.slice(0, 2).map((format) => (
                    <span key={format}>{format.toUpperCase()}</span>
                  ))}
                </div>
                <div className={styles.reasons}>
                  <strong>Why it matched</strong>
                  <ul>
                    {item.reasons.map((reason) => (
                      <li key={reason.claim_id}>
                        <span className={styles.reasonSignal}>{signalLabel(reason.signal)}</span>
                        <span>{reason.matched_values.join(", ")}</span>
                        <span className={styles.reasonAuthority}>
                          {reason.source}{reason.source === "inferred" ? ` · ${Math.round(reason.confidence * 100)}%` : ""}
                        </span>
                        <span className={styles.contribution}>+{reason.contribution.toFixed(2)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div className={styles.actions}>
                  <a href={dossierHref(item)}>View dossier</a>
                  <a href="/personalization">Manage preferences</a>
                </div>
              </article>
            ))}
          </div>
          <p className={styles.footnote}>{response.explanation}</p>
        </>
      ) : null}
    </section>
  );
}

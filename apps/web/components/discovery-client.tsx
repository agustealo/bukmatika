"use client";

import { FormEvent, useMemo, useState } from "react";

import styles from "./discovery-client.module.css";

type RightsState =
  | "public_domain"
  | "open_license"
  | "authorized_download"
  | "borrow_only"
  | "preview_only"
  | "unknown"
  | "restricted";

type RightsEvidence = {
  state: RightsState;
  source: string;
  basis: string;
};

type Candidate = {
  source: string;
  source_record_id: string;
  work_key: string;
  title: string;
  authors: string[];
  first_publish_year: number | null;
  languages: string[];
  subjects: string[];
  landing_url: string;
  formats: string[];
  rights: RightsEvidence[];
};

type RankingExplanation = {
  neutral_score: number;
  preference_boost: number;
  total_score: number;
  matched_preferences: Array<"language" | "format" | "era" | "rights" | "source">;
};

type DiscoveryResponse = {
  candidates: Candidate[];
  ranking: Record<string, RankingExplanation>;
  sources_queried: string[];
  source_errors: Record<string, string>;
};

type DiscoveryPreferences = {
  languages: string[];
  formats: string[];
  year_from?: number;
  year_to?: number;
  rights_states: RightsState[];
  sources: string[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

const RIGHTS_OPTIONS: Array<{ value: RightsState; label: string }> = [
  { value: "public_domain", label: "Public domain" },
  { value: "open_license", label: "Open license" },
  { value: "authorized_download", label: "Authorized download" },
  { value: "borrow_only", label: "Borrow only" },
  { value: "preview_only", label: "Preview only" },
  { value: "unknown", label: "Unknown rights" },
  { value: "restricted", label: "Restricted" },
];

const SOURCE_OPTIONS = [
  { value: "open_library", label: "Open Library" },
  { value: "internet_archive", label: "Internet Archive" },
  { value: "project_gutenberg", label: "Project Gutenberg" },
  { value: "library_of_congress", label: "Library of Congress" },
];

function rightsLabel(candidate: Candidate): string {
  const state = candidate.rights[0]?.state ?? "unknown";
  return state.replaceAll("_", " ");
}

function dossierHref(candidate: Candidate): string {
  const params = new URLSearchParams({
    provider: candidate.source,
    record_id: candidate.source_record_id,
  });
  return `/dossier?${params.toString()}`;
}

function candidateRankingKey(candidate: Candidate): string {
  return `${candidate.source}:${candidate.source_record_id}`;
}

function commaList(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function optionalYear(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number.parseInt(value, 10);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 3000 ? parsed : undefined;
}

function preferenceDimensionCount(preferences: DiscoveryPreferences): number {
  return [
    preferences.languages.length > 0,
    preferences.formats.length > 0,
    preferences.year_from !== undefined || preferences.year_to !== undefined,
    preferences.rights_states.length > 0,
    preferences.sources.length > 0,
  ].filter(Boolean).length;
}

export function DiscoveryClient() {
  const [query, setQuery] = useState("");
  const [languages, setLanguages] = useState("");
  const [format, setFormat] = useState("");
  const [yearFrom, setYearFrom] = useState("");
  const [yearTo, setYearTo] = useState("");
  const [rightsState, setRightsState] = useState<RightsState | "">("");
  const [source, setSource] = useState("");
  const [results, setResults] = useState<DiscoveryResponse | null>(null);
  const [appliedPreferenceCount, setAppliedPreferenceCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const countLabel = useMemo(() => {
    if (!results) return null;
    return `${results.candidates.length} result${results.candidates.length === 1 ? "" : "s"}`;
  }, [results]);

  const activePreferenceCount = useMemo(
    () =>
      [
        languages.trim(),
        format,
        yearFrom || yearTo,
        rightsState,
        source,
      ].filter(Boolean).length,
    [languages, format, yearFrom, yearTo, rightsState, source],
  );

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized || loading) return;

    const preferredYearFrom = optionalYear(yearFrom);
    const preferredYearTo = optionalYear(yearTo);
    if (yearFrom.trim() && preferredYearFrom === undefined) {
      setError("Preferred start year must be between 1 and 3000.");
      return;
    }
    if (yearTo.trim() && preferredYearTo === undefined) {
      setError("Preferred end year must be between 1 and 3000.");
      return;
    }
    if (
      preferredYearFrom !== undefined &&
      preferredYearTo !== undefined &&
      preferredYearFrom > preferredYearTo
    ) {
      setError("Preferred start year cannot be later than the preferred end year.");
      return;
    }

    const preferences: DiscoveryPreferences = {
      languages: commaList(languages),
      formats: format ? [format] : [],
      rights_states: rightsState ? [rightsState] : [],
      sources: source ? [source] : [],
      ...(preferredYearFrom === undefined ? {} : { year_from: preferredYearFrom }),
      ...(preferredYearTo === undefined ? {} : { year_to: preferredYearTo }),
    };
    const submittedPreferenceCount = preferenceDimensionCount(preferences);

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: normalized, preferences, limit: 24 }),
      });
      if (!response.ok) {
        throw new Error(`Discovery failed with HTTP ${response.status}.`);
      }
      setResults((await response.json()) as DiscoveryResponse);
      setAppliedPreferenceCount(submittedPreferenceCount);
    } catch (caught) {
      setResults(null);
      setAppliedPreferenceCount(0);
      setError(caught instanceof Error ? caught.message : "Discovery failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="discovery-panel" aria-label="Book discovery">
      <form className="search-form" onSubmit={submit}>
        <label htmlFor="discovery-query">What do you want to build a library about?</label>
        <div className="search-row">
          <input
            id="discovery-query"
            name="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Old World before Columbus, maritime trade, ancient astronomy…"
            autoComplete="off"
          />
          <button type="submit" disabled={loading || !query.trim()}>
            {loading ? "Searching…" : "Discover"}
          </button>
        </div>

        <details className={styles.preferences}>
          <summary>
            Tune ranking{activePreferenceCount > 0 ? ` · ${activePreferenceCount} set` : ""}
          </summary>
          <div className={styles.preferenceGrid}>
            <div className={styles.field}>
              <label htmlFor="preferred-languages">Language</label>
              <input
                id="preferred-languages"
                value={languages}
                onChange={(event) => setLanguages(event.target.value)}
                placeholder="English, Latin, fr…"
                autoComplete="off"
              />
            </div>

            <div className={styles.field}>
              <label htmlFor="preferred-format">Format</label>
              <select
                id="preferred-format"
                value={format}
                onChange={(event) => setFormat(event.target.value)}
              >
                <option value="">Any format</option>
                <option value="PDF">PDF</option>
                <option value="EPUB">EPUB</option>
                <option value="TXT">TXT</option>
                <option value="HTML">HTML</option>
                <option value="DOCX">DOCX</option>
              </select>
            </div>

            <div className={styles.field}>
              <label htmlFor="preferred-rights">Rights state</label>
              <select
                id="preferred-rights"
                value={rightsState}
                onChange={(event) => setRightsState(event.target.value as RightsState | "")}
              >
                <option value="">Any rights state</option>
                {RIGHTS_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div className={styles.field}>
              <label htmlFor="preferred-source">Source</label>
              <select
                id="preferred-source"
                value={source}
                onChange={(event) => setSource(event.target.value)}
              >
                <option value="">Any source</option>
                {SOURCE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>

            <div className={styles.field}>
              <label htmlFor="preferred-year-from">Era</label>
              <div className={styles.eraFields}>
                <input
                  id="preferred-year-from"
                  type="number"
                  min="1"
                  max="3000"
                  inputMode="numeric"
                  value={yearFrom}
                  onChange={(event) => setYearFrom(event.target.value)}
                  placeholder="From"
                  aria-label="Preferred start year"
                />
                <input
                  type="number"
                  min="1"
                  max="3000"
                  inputMode="numeric"
                  value={yearTo}
                  onChange={(event) => setYearTo(event.target.value)}
                  placeholder="To"
                  aria-label="Preferred end year"
                />
              </div>
            </div>
          </div>
          <p className={styles.preferenceNote}>
            Preferences are soft ranking signals, never access rules. Each matched dimension adds
            0.01 and the total preference boost is capped at 0.05. All enabled sources are still
            searched and rights evidence is never rewritten.
          </p>
        </details>

        <p className="search-note">
          Live federated discovery queries Open Library, Internet Archive, Project Gutenberg,
          and the Library of Congress. Source failures degrade independently.
        </p>
      </form>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {results ? (
        <div className="results-wrap">
          <div className="results-meta">
            <strong>{countLabel}</strong>
            <span>Sources: {results.sources_queried.join(", ")}</span>
            {appliedPreferenceCount > 0 ? (
              <span className={styles.preferenceDisclosure}>Preference boost ≤ 0.05</span>
            ) : null}
          </div>

          {Object.keys(results.source_errors).length > 0 ? (
            <div className="source-warning">
              Some sources degraded: {Object.keys(results.source_errors).join(", ")}
            </div>
          ) : null}

          <div className="result-grid">
            {results.candidates.map((candidate) => {
              const ranking = results.ranking[candidateRankingKey(candidate)];
              return (
                <article className="book-card" key={`${candidate.source}:${candidate.source_record_id}`}>
                  <div className="book-card-topline">
                    <span className={`rights-chip rights-${candidate.rights[0]?.state ?? "unknown"}`}>
                      {rightsLabel(candidate)}
                    </span>
                    <span className="source-label">{candidate.source.replaceAll("_", " ")}</span>
                  </div>
                  <h2>{candidate.title}</h2>
                  <p className="byline">
                    {candidate.authors.length > 0 ? candidate.authors.join(", ") : "Unknown author"}
                  </p>
                  <div className="book-facts">
                    {candidate.first_publish_year ? <span>{candidate.first_publish_year}</span> : null}
                    {candidate.languages.slice(0, 3).map((language) => (
                      <span key={language}>{language.toUpperCase()}</span>
                    ))}
                    {candidate.formats.slice(0, 2).map((candidateFormat) => (
                      <span key={candidateFormat}>{candidateFormat.toUpperCase()}</span>
                    ))}
                  </div>
                  {ranking ? (
                    <p className={styles.rankingNote}>
                      Neutral {ranking.neutral_score.toFixed(2)} · preference +
                      {ranking.preference_boost.toFixed(2)}
                      {ranking.matched_preferences.length > 0 ? (
                        <span className={styles.rankingMatches}>
                          {` · ${ranking.matched_preferences.join(" · ")}`}
                        </span>
                      ) : null}
                    </p>
                  ) : null}
                  {candidate.subjects.length > 0 ? (
                    <p className="subjects">{candidate.subjects.slice(0, 4).join(" · ")}</p>
                  ) : null}
                  <div className="book-actions">
                    <a className="primary-card-link" href={dossierHref(candidate)}>
                      View dossier
                    </a>
                    <a className="secondary-link" href={candidate.landing_url} target="_blank" rel="noreferrer">
                      Source <span aria-hidden="true">↗</span>
                    </a>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      ) : null}
    </section>
  );
}

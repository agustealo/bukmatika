"use client";

import { FormEvent, useMemo, useState } from "react";

type RightsEvidence = {
  state:
    | "public_domain"
    | "open_license"
    | "authorized_download"
    | "borrow_only"
    | "preview_only"
    | "unknown"
    | "restricted";
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
  rights: RightsEvidence[];
};

type DiscoveryResponse = {
  candidates: Candidate[];
  sources_queried: string[];
  source_errors: Record<string, string>;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function rightsLabel(candidate: Candidate): string {
  const state = candidate.rights[0]?.state ?? "unknown";
  return state.replaceAll("_", " ");
}

export function DiscoveryClient() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<DiscoveryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const countLabel = useMemo(() => {
    if (!results) return null;
    return `${results.candidates.length} result${results.candidates.length === 1 ? "" : "s"}`;
  }, [results]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized || loading) return;

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/v1/discover`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: normalized, limit: 24 }),
      });
      if (!response.ok) {
        throw new Error(`Discovery failed with HTTP ${response.status}.`);
      }
      setResults((await response.json()) as DiscoveryResponse);
    } catch (caught) {
      setResults(null);
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
        <p className="search-note">
          Live results currently use the Open Library adapter. More source adapters land as
          independent vertical slices.
        </p>
      </form>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {results ? (
        <div className="results-wrap">
          <div className="results-meta">
            <strong>{countLabel}</strong>
            <span>Sources: {results.sources_queried.join(", ")}</span>
          </div>

          {Object.keys(results.source_errors).length > 0 ? (
            <div className="source-warning">
              Some sources degraded: {Object.keys(results.source_errors).join(", ")}
            </div>
          ) : null}

          <div className="result-grid">
            {results.candidates.map((candidate) => (
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
                </div>
                {candidate.subjects.length > 0 ? (
                  <p className="subjects">{candidate.subjects.slice(0, 4).join(" · ")}</p>
                ) : null}
                <a href={candidate.landing_url} target="_blank" rel="noreferrer">
                  Open source record <span aria-hidden="true">↗</span>
                </a>
              </article>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}

"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import { ResearchDelegationComposer } from "./research-delegation-composer";

type LibraryItem = {
  library_entry_id: string;
  work_id: string;
  edition_id: string | null;
  title: string;
  authors: string[];
  status: string;
  readable_document_id: string | null;
  readable_format: string | null;
  progress_fraction: number | null;
  reading_status: string | null;
};

type LibraryResponse = {
  items: LibraryItem[];
};

type ResearchPassage = {
  library_entry_id: string;
  work_id: string;
  work_title: string;
  edition_id: string;
  edition_title: string;
  asset_id: string;
  document_id: string;
  chunk_id: string;
  section_id: string;
  section_ordinal: number;
  chunk_ordinal: number;
  heading: string | null;
  locator: Record<string, string | number>;
  char_start: number;
  char_end: number;
  text: string;
  score: number;
};

type ResearchSearchResponse = {
  query: string;
  selected_library_entry_ids: string[];
  passages: ResearchPassage[];
};

type ResearchComparisonEdition = {
  edition_id: string;
  edition_title: string;
};

type ResearchComparisonSource = {
  library_entry_id: string;
  work_id: string;
  work_title: string;
  available_editions: ResearchComparisonEdition[];
  passages: ResearchPassage[];
};

type ResearchCompareResponse = {
  query: string;
  selected_library_entry_ids: string[];
  per_source_limit: number;
  sources: ResearchComparisonSource[];
};

type ResearchMode = "search" | "compare";

const SEARCH_MAX_SELECTIONS = 20;
const COMPARE_MAX_SELECTIONS = 6;
const COMPARE_PASSAGES_PER_SOURCE = 3;

function locatorLabel(locator: Record<string, string | number>): string {
  const priority = ["page", "spine", "section", "paragraph", "row"];
  const parts = priority
    .filter((key) => locator[key] !== undefined)
    .map((key) => `${key} ${String(locator[key])}`);
  return parts.length > 0 ? parts.join(" · ") : "Exact source coordinate";
}

function selectionLabel(count: number): string {
  return `${count} source${count === 1 ? "" : "s"} selected`;
}

function passageReaderHref(passage: ResearchPassage): string {
  return `/read/${passage.library_entry_id}/${passage.document_id}#reader-section-${passage.section_id}`;
}

export function ResearchClient() {
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<ResearchMode>("search");
  const [searchResults, setSearchResults] = useState<ResearchSearchResponse | null>(null);
  const [compareResults, setCompareResults] = useState<ResearchCompareResponse | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readable = useMemo(
    () => library.filter((item) => item.readable_document_id !== null),
    [library],
  );
  const selectionLimit = mode === "compare" ? COMPARE_MAX_SELECTIONS : SEARCH_MAX_SELECTIONS;
  const minimumSelection = mode === "compare" ? 2 : 1;

  useEffect(() => {
    let cancelled = false;
    async function loadLibrary() {
      setLoadingLibrary(true);
      setError(null);
      try {
        const response = await apiFetch("/v1/library", { cache: "no-store" });
        if (!response.ok) throw new Error(`Library failed with HTTP ${response.status}.`);
        const body = (await response.json()) as LibraryResponse;
        if (!cancelled) setLibrary(body.items);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Could not load your library.");
        }
      } finally {
        if (!cancelled) setLoadingLibrary(false);
      }
    }
    void loadLibrary();
    return () => {
      cancelled = true;
    };
  }, []);

  function changeMode(nextMode: ResearchMode) {
    setMode(nextMode);
    setError(null);
    if (nextMode === "compare") {
      setSelected((current) => current.slice(0, COMPARE_MAX_SELECTIONS));
    }
  }

  function toggleEntry(entryId: string) {
    setSelected((current) => {
      if (current.includes(entryId)) {
        return current.filter((id) => id !== entryId);
      }
      if (current.length >= selectionLimit) return current;
      return [...current, entryId];
    });
  }

  function selectAllReadable() {
    setSelected(readable.slice(0, selectionLimit).map((item) => item.library_entry_id));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized || selected.length < minimumSelection || running) return;

    setRunning(true);
    setError(null);
    try {
      if (mode === "compare") {
        const response = await apiFetch("/v1/research/compare", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: normalized,
            library_entry_ids: selected,
            per_source_limit: COMPARE_PASSAGES_PER_SOURCE,
          }),
        });
        if (!response.ok) {
          throw new Error(`Source comparison failed with HTTP ${response.status}.`);
        }
        setCompareResults((await response.json()) as ResearchCompareResponse);
      } else {
        const response = await apiFetch("/v1/research/search", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: normalized,
            library_entry_ids: selected,
            limit: 30,
          }),
        });
        if (!response.ok) {
          throw new Error(`Research search failed with HTTP ${response.status}.`);
        }
        setSearchResults((await response.json()) as ResearchSearchResponse);
      }
    } catch (caught) {
      if (mode === "compare") {
        setCompareResults(null);
      } else {
        setSearchResults(null);
      }
      setError(caught instanceof Error ? caught.message : "Research request failed.");
    } finally {
      setRunning(false);
    }
  }

  const activeResults = mode === "compare" ? compareResults : searchResults;

  return (
    <section className="research-surface" aria-label="Selected-book research">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Grounded research</p>
          <h1>Search evidence. Compare sources.</h1>
          <p>
            Work directly with canonical text from books in your library. Search across a broad
            selection or compare two to six sources with an equal evidence budget for each one.
          </p>
        </div>
      </div>

      <div className="research-mode-switch" role="group" aria-label="Research mode">
        <button
          type="button"
          className={mode === "search" ? "is-active" : ""}
          aria-pressed={mode === "search"}
          onClick={() => changeMode("search")}
        >
          Search passages
        </button>
        <button
          type="button"
          className={mode === "compare" ? "is-active" : ""}
          aria-pressed={mode === "compare"}
          onClick={() => changeMode("compare")}
        >
          Compare sources
        </button>
      </div>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {loadingLibrary ? (
        <div className="surface-loading" aria-busy="true">Loading readable books…</div>
      ) : library.length === 0 ? (
        <div className="empty-surface">
          <strong>Your library is empty.</strong>
          <p>Discover and save books before starting grounded research.</p>
          <a className="primary-action link-button" href="/">Discover books</a>
        </div>
      ) : (
        <div className="research-layout">
          <aside className="research-selection" aria-label="Research source selection">
            <div className="research-selection-heading">
              <div>
                <span className="reader-meta-label">Sources</span>
                <strong>{selectionLabel(selected.length)}</strong>
                <small>
                  {mode === "compare"
                    ? `Choose 2–${COMPARE_MAX_SELECTIONS} readable sources.`
                    : `Choose up to ${SEARCH_MAX_SELECTIONS} readable sources.`}
                </small>
              </div>
              <div className="research-selection-actions">
                <button type="button" onClick={selectAllReadable} disabled={readable.length === 0}>
                  Select readable
                </button>
                {selected.length > 0 ? (
                  <button type="button" onClick={() => setSelected([])}>Clear</button>
                ) : null}
              </div>
            </div>

            <div className="research-source-list">
              {library.map((item) => {
                const readableItem = item.readable_document_id !== null;
                const checked = selected.includes(item.library_entry_id);
                const atLimit = selected.length >= selectionLimit && !checked;
                return (
                  <label
                    className={`research-source ${readableItem ? "" : "research-source-disabled"}`}
                    key={item.library_entry_id}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!readableItem || atLimit}
                      onChange={() => toggleEntry(item.library_entry_id)}
                    />
                    <span>
                      <strong>{item.title}</strong>
                      <small>
                        {item.authors.length > 0 ? item.authors.join(", ") : "Unknown author"}
                        {readableItem
                          ? ` · ${item.readable_format ?? "Processed text"}`
                          : " · Not processed yet"}
                      </small>
                    </span>
                  </label>
                );
              })}
            </div>
          </aside>

          <div className="research-main">
            <form className="research-query" onSubmit={submit}>
              <label htmlFor="research-query">
                {mode === "compare"
                  ? "What evidence should Bukmatika compare across these sources?"
                  : "What should Bukmatika find in these books?"}
              </label>
              <div className="search-row">
                <input
                  id="research-query"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="maritime trade before 1492, treaty language, astronomical navigation…"
                  autoComplete="off"
                />
                <button
                  type="submit"
                  disabled={running || selected.length < minimumSelection || !query.trim()}
                >
                  {running
                    ? mode === "compare" ? "Comparing…" : "Searching…"
                    : mode === "compare" ? "Compare evidence" : "Search passages"}
                </button>
              </div>
              <p className="search-note">
                {mode === "compare"
                  ? "Each source is searched independently with the same bounded passage limit. Empty columns mean no exact lexical match, not missing data."
                  : "Results are lexical matches from PostgreSQL full-text search over only the selected owned documents. Every result resolves back to canonical source text."}
              </p>
            </form>

            <ResearchDelegationComposer
              libraryEntryIds={selected}
              researchRequest={query}
            />

            {mode === "search" && searchResults ? (
              <div className="research-results" aria-live="polite">
                <div className="results-meta">
                  <strong>
                    {searchResults.passages.length} passage
                    {searchResults.passages.length === 1 ? "" : "s"}
                  </strong>
                  <span>{selectionLabel(searchResults.selected_library_entry_ids.length)}</span>
                </div>

                {searchResults.passages.length === 0 ? (
                  <div className="empty-surface">
                    <strong>No exact text matches.</strong>
                    <p>Try different terms or widen the selected books.</p>
                  </div>
                ) : (
                  <div className="passage-list">
                    {searchResults.passages.map((passage) => (
                      <article className="passage-card" key={passage.chunk_id}>
                        <div className="passage-citation">
                          <span>{passage.work_title}</span>
                          <span>{passage.edition_title}</span>
                          <span>{locatorLabel(passage.locator)}</span>
                        </div>
                        {passage.heading ? <h2>{passage.heading}</h2> : null}
                        <p>{passage.text}</p>
                        <div className="passage-actions">
                          <span>Text offsets {passage.char_start}–{passage.char_end}</span>
                          <a href={passageReaderHref(passage)}>Open cited passage</a>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </div>
            ) : null}

            {mode === "compare" && compareResults ? (
              <div className="research-results comparison-results" aria-live="polite">
                <div className="results-meta">
                  <strong>
                    {compareResults.sources.length} source
                    {compareResults.sources.length === 1 ? "" : "s"} compared
                  </strong>
                  <span>Up to {compareResults.per_source_limit} passages per source</span>
                </div>

                <div className="comparison-grid">
                  {compareResults.sources.map((source) => (
                    <section className="comparison-source" key={source.library_entry_id}>
                      <header className="comparison-source-heading">
                        <span className="reader-meta-label">Source</span>
                        <h2>{source.work_title}</h2>
                        <div className="comparison-editions" aria-label="Processed editions">
                          {source.available_editions.map((edition) => (
                            <span key={edition.edition_id}>{edition.edition_title}</span>
                          ))}
                        </div>
                      </header>

                      {source.passages.length === 0 ? (
                        <div className="comparison-no-match">
                          <strong>No exact lexical match</strong>
                          <p>This source stays visible so absence of matching evidence is explicit.</p>
                        </div>
                      ) : (
                        <div className="comparison-passages">
                          {source.passages.map((passage) => (
                            <article className="comparison-passage" key={passage.chunk_id}>
                              <div className="passage-citation">
                                <span>{passage.edition_title}</span>
                                <span>{locatorLabel(passage.locator)}</span>
                              </div>
                              {passage.heading ? <h3>{passage.heading}</h3> : null}
                              <p>{passage.text}</p>
                              <div className="passage-actions">
                                <span>{passage.char_start}–{passage.char_end}</span>
                                <a href={passageReaderHref(passage)}>Open source</a>
                              </div>
                            </article>
                          ))}
                        </div>
                      )}
                    </section>
                  ))}
                </div>
              </div>
            ) : null}

            {!activeResults ? (
              <div className="research-empty-state">
                <span className="reader-meta-label">Evidence first</span>
                <h2>{mode === "compare" ? "Put sources next to each other." : "Start with the text."}</h2>
                <p>
                  {mode === "compare"
                    ? "Bukmatika keeps each selected source visible, retrieves evidence independently, and preserves exact edition and reader coordinates."
                    : "Search returns inspectable canonical passages before any reasoning layer is involved. Grounded answers can build on this evidence without replacing it."}
                </p>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}

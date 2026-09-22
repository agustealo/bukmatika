"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";

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

const MAX_SELECTIONS = 20;

function locatorLabel(locator: Record<string, string | number>): string {
  const priority = ["page", "spine", "section", "paragraph", "row"];
  const parts = priority
    .filter((key) => locator[key] !== undefined)
    .map((key) => `${key} ${String(locator[key])}`);
  return parts.length > 0 ? parts.join(" · ") : "Exact source coordinate";
}

function selectionLabel(count: number): string {
  return `${count} book${count === 1 ? "" : "s"} selected`;
}

export function ResearchClient() {
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ResearchSearchResponse | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readable = useMemo(
    () => library.filter((item) => item.readable_document_id !== null),
    [library],
  );

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

  function toggleEntry(entryId: string) {
    setSelected((current) => {
      if (current.includes(entryId)) {
        return current.filter((id) => id !== entryId);
      }
      if (current.length >= MAX_SELECTIONS) return current;
      return [...current, entryId];
    });
  }

  function selectAllReadable() {
    setSelected(readable.slice(0, MAX_SELECTIONS).map((item) => item.library_entry_id));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized || selected.length === 0 || searching) return;

    setSearching(true);
    setError(null);
    try {
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
      setResults((await response.json()) as ResearchSearchResponse);
    } catch (caught) {
      setResults(null);
      setError(caught instanceof Error ? caught.message : "Research search failed.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <section className="research-surface" aria-label="Selected-book research">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Grounded research</p>
          <h1>Search the books you actually own.</h1>
          <p>
            Select up to {MAX_SELECTIONS} readable books. Bukmatika searches their canonical text
            and returns exact passages with source coordinates. No AI synthesis is used here.
          </p>
        </div>
      </div>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {loadingLibrary ? (
        <div className="surface-loading" aria-busy="true">Loading readable books…</div>
      ) : library.length === 0 ? (
        <div className="empty-surface">
          <strong>Your library is empty.</strong>
          <p>Discover and save books before starting a grounded research search.</p>
          <a className="primary-action link-button" href="/">Discover books</a>
        </div>
      ) : (
        <div className="research-layout">
          <aside className="research-selection" aria-label="Research book selection">
            <div className="research-selection-heading">
              <div>
                <span className="reader-meta-label">Sources</span>
                <strong>{selectionLabel(selected.length)}</strong>
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
                const atLimit = selected.length >= MAX_SELECTIONS && !checked;
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
              <label htmlFor="research-query">What should Bukmatika find in these books?</label>
              <div className="search-row">
                <input
                  id="research-query"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="maritime trade before 1492, astronomical navigation, treaty language…"
                  autoComplete="off"
                />
                <button
                  type="submit"
                  disabled={searching || selected.length === 0 || !query.trim()}
                >
                  {searching ? "Searching…" : "Search passages"}
                </button>
              </div>
              <p className="search-note">
                Results are lexical matches from PostgreSQL full-text search over only the selected
                owned documents. Every result resolves back to canonical source text.
              </p>
            </form>

            {results ? (
              <div className="research-results" aria-live="polite">
                <div className="results-meta">
                  <strong>
                    {results.passages.length} passage{results.passages.length === 1 ? "" : "s"}
                  </strong>
                  <span>{selectionLabel(results.selected_library_entry_ids.length)}</span>
                </div>

                {results.passages.length === 0 ? (
                  <div className="empty-surface">
                    <strong>No exact text matches.</strong>
                    <p>Try different terms or widen the selected books.</p>
                  </div>
                ) : (
                  <div className="passage-list">
                    {results.passages.map((passage) => (
                      <article className="passage-card" key={passage.chunk_id}>
                        <div className="passage-citation">
                          <span>{passage.work_title}</span>
                          <span>{passage.edition_title}</span>
                          <span>{locatorLabel(passage.locator)}</span>
                        </div>
                        {passage.heading ? <h2>{passage.heading}</h2> : null}
                        <p>{passage.text}</p>
                        <div className="passage-actions">
                          <span>
                            Text offsets {passage.char_start}–{passage.char_end}
                          </span>
                          <a
                            href={`/read/${passage.library_entry_id}/${passage.document_id}#reader-section-${passage.section_id}`}
                          >
                            Open cited passage
                          </a>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="research-empty-state">
                <span className="reader-meta-label">Exact evidence first</span>
                <h2>No synthesis layer yet.</h2>
                <p>
                  This workspace deliberately shows the retrieval evidence before we add grounded
                  question answering. The future reasoning layer must cite these canonical passages.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

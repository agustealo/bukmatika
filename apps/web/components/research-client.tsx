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

type GroundedCitation = {
  evidence_id: string;
  library_entry_id: string;
  work_id: string;
  work_title: string;
  edition_id: string;
  edition_title: string;
  document_id: string;
  chunk_id: string;
  section_id: string;
  heading: string | null;
  locator: Record<string, string | number>;
  char_start: number;
  char_end: number;
  text: string;
};

type GroundedClaim = {
  text: string;
  citations: GroundedCitation[];
};

type GroundedAnswerResponse = {
  status: "grounded" | "no_evidence" | "insufficient_evidence";
  question: string;
  selected_library_entry_ids: string[];
  retrieval_count: number;
  claims: GroundedClaim[];
  provider: string | null;
  model: string | null;
};

type ProductErrorPayload = {
  detail?: string | { code?: string };
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

async function groundedErrorMessage(response: Response): Promise<string> {
  let code: string | null = null;
  try {
    const payload = (await response.json()) as ProductErrorPayload;
    if (typeof payload.detail === "object" && payload.detail !== null) {
      code = payload.detail.code ?? null;
    } else if (typeof payload.detail === "string") {
      return payload.detail;
    }
  } catch {
    // Fall through to the status-based message.
  }

  if (code === "AI_DISABLED") {
    return "AI assistance is disabled. Exact passage search remains available.";
  }
  if (code === "MODEL_PROVIDER_UNCONFIGURED") {
    return "AI synthesis is not configured. Exact passage search remains available.";
  }
  if (code === "RESEARCH_SELECTION_UNAVAILABLE") {
    return "One or more selected books are no longer available to this library profile.";
  }
  if (code === "GROUNDED_CITATION_INVALID") {
    return "The generated answer failed citation validation and was not shown.";
  }
  return `Grounded answer failed with HTTP ${response.status}. Passage search is still available.`;
}

export function ResearchClient() {
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ResearchSearchResponse | null>(null);
  const [answer, setAnswer] = useState<GroundedAnswerResponse | null>(null);
  const [loadingLibrary, setLoadingLibrary] = useState(true);
  const [searching, setSearching] = useState(false);
  const [answering, setAnswering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [aiError, setAiError] = useState<string | null>(null);

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

  function resetResearchOutputs() {
    setResults(null);
    setAnswer(null);
    setAiError(null);
  }

  function toggleEntry(entryId: string) {
    setSelected((current) => {
      if (current.includes(entryId)) {
        return current.filter((id) => id !== entryId);
      }
      if (current.length >= MAX_SELECTIONS) return current;
      return [...current, entryId];
    });
    resetResearchOutputs();
  }

  function selectAllReadable() {
    setSelected(readable.slice(0, MAX_SELECTIONS).map((item) => item.library_entry_id));
    resetResearchOutputs();
  }

  function clearSelection() {
    setSelected([]);
    resetResearchOutputs();
  }

  function updateQuery(value: string) {
    setQuery(value);
    resetResearchOutputs();
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

  async function askGrounded() {
    const normalized = query.trim();
    if (!normalized || selected.length === 0 || answering) return;

    setAnswering(true);
    setAiError(null);
    try {
      const response = await apiFetch("/v1/research/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: normalized,
          library_entry_ids: selected,
          max_passages: 12,
        }),
      });
      if (!response.ok) {
        setAnswer(null);
        setAiError(await groundedErrorMessage(response));
        return;
      }
      setAnswer((await response.json()) as GroundedAnswerResponse);
    } catch (caught) {
      setAnswer(null);
      setAiError(
        caught instanceof Error
          ? `${caught.message} Passage search is still available.`
          : "Grounded answer failed. Passage search is still available.",
      );
    } finally {
      setAnswering(false);
    }
  }

  return (
    <section className="research-surface" aria-label="Selected-book research">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Grounded research</p>
          <h1>Research the books you actually own.</h1>
          <p>
            Select up to {MAX_SELECTIONS} readable books. Search their canonical text directly or
            ask for an AI-assisted answer whose claims must resolve to exact retrieved passages.
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
                  <button type="button" onClick={clearSelection}>Clear</button>
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
              <label htmlFor="research-query">What should Bukmatika investigate?</label>
              <div className="search-row">
                <input
                  id="research-query"
                  value={query}
                  onChange={(event) => updateQuery(event.target.value)}
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
              <div className="research-query-actions">
                <button
                  type="button"
                  className="secondary-action"
                  disabled={answering || selected.length === 0 || !query.trim()}
                  onClick={() => void askGrounded()}
                >
                  {answering ? "Grounding answer…" : "Ask with AI"}
                </button>
                <span>
                  AI synthesis is optional. Passage search remains available when AI is disabled or
                  no model provider is configured.
                </span>
              </div>
              <p className="search-note">
                Retrieval is PostgreSQL full-text search over only the selected owned documents.
                Grounded answers are validated against the exact retrieved chunk IDs before display.
              </p>
            </form>

            {aiError ? (
              <div className="research-ai-warning" role="status">
                <strong>AI answer unavailable</strong>
                <span>{aiError}</span>
              </div>
            ) : null}

            {answer ? <GroundedAnswer answer={answer} /> : null}

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
            ) : !answer ? (
              <div className="research-empty-state">
                <span className="reader-meta-label">Evidence first</span>
                <h2>Search directly, or ask a grounded question.</h2>
                <p>
                  The same principal-scoped retrieval spine powers both paths. AI answers cannot
                  introduce a citation that was not present in the selected-book retrieval set.
                </p>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}

function GroundedAnswer({ answer }: { answer: GroundedAnswerResponse }) {
  if (answer.status === "no_evidence") {
    return (
      <section className="grounded-answer grounded-answer-empty" aria-live="polite">
        <span className="reader-meta-label">Grounded answer</span>
        <h2>No supporting passage was retrieved.</h2>
        <p>Bukmatika did not call the model because the selected books produced no evidence.</p>
      </section>
    );
  }

  if (answer.status === "insufficient_evidence") {
    return (
      <section className="grounded-answer grounded-answer-empty" aria-live="polite">
        <span className="reader-meta-label">Grounded answer</span>
        <h2>The retrieved evidence is not enough to answer safely.</h2>
        <p>
          The synthesis layer declined to make a claim. Try a narrower question or inspect the exact
          passages directly.
        </p>
      </section>
    );
  }

  return (
    <section className="grounded-answer" aria-live="polite">
      <div className="grounded-answer-heading">
        <div>
          <span className="reader-meta-label">Grounded answer</span>
          <h2>Answer from selected-book evidence</h2>
        </div>
        <span className="grounded-provider">
          {answer.provider && answer.model
            ? `${answer.provider} · ${answer.model}`
            : "Configured model gateway"}
        </span>
      </div>

      <div className="grounded-claim-list">
        {answer.claims.map((claim, claimIndex) => (
          <article className="grounded-claim" key={`${claimIndex}-${claim.text}`}>
            <p>{claim.text}</p>
            <div className="grounded-citations" aria-label={`Citations for claim ${claimIndex + 1}`}>
              {claim.citations.map((citation, citationIndex) => (
                <a
                  key={citation.evidence_id}
                  href={`/read/${citation.library_entry_id}/${citation.document_id}#reader-section-${citation.section_id}`}
                >
                  <strong>[{claimIndex + 1}.{citationIndex + 1}] {citation.work_title}</strong>
                  <span>{citation.edition_title} · {locatorLabel(citation.locator)}</span>
                  <small>{citation.text}</small>
                </a>
              ))}
            </div>
          </article>
        ))}
      </div>

      <div className="grounded-answer-footer">
        <span>{answer.retrieval_count} retrieved passage packets validated for this answer.</span>
        <span>{selectionLabel(answer.selected_library_entry_ids.length)}</span>
      </div>
    </section>
  );
}

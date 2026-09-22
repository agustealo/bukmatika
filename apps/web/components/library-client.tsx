"use client";

import { useCallback, useEffect, useState } from "react";

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

export function LibraryClient() {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const response = await apiFetch("/v1/library", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Library failed with HTTP ${response.status}.`);
    }
    const body = (await response.json()) as LibraryResponse;
    setItems(body.items);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const response = await apiFetch("/v1/library", { cache: "no-store" });
        if (!response.ok) throw new Error(`Library failed with HTTP ${response.status}.`);
        const body = (await response.json()) as LibraryResponse;
        if (!cancelled) setItems(body.items);
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Library failed.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return <div className="surface-loading" aria-busy="true">Opening your library…</div>;
  }

  return (
    <section className="library-surface" aria-label="Personal library">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Your library</p>
          <h1>Books you have chosen to keep.</h1>
          <p>Reading state lives with the canonical edition and processed document, not the browser.</p>
        </div>
        <button
          className="secondary-action"
          type="button"
          onClick={() => {
            setError(null);
            void load().catch((caught: unknown) => {
              setError(caught instanceof Error ? caught.message : "Library refresh failed.");
            });
          }}
        >
          Refresh
        </button>
      </div>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {items.length === 0 ? (
        <div className="empty-surface">
          <strong>Your shelves are empty.</strong>
          <p>Discover a work, inspect its editions, then save the work or the exact edition you want.</p>
          <a className="primary-action link-button" href="/">Discover books</a>
        </div>
      ) : (
        <div className="library-grid">
          {items.map((item) => {
            const progress = Math.round((item.progress_fraction ?? 0) * 100);
            return (
              <article className="library-card" key={item.library_entry_id}>
                <div className="book-card-topline">
                  <span className="saved-chip">{item.edition_id ? "Edition" : "Work"}</span>
                  {item.readable_format ? <span className="source-label">{item.readable_format}</span> : null}
                </div>
                <h2>{item.title}</h2>
                <p className="byline">
                  {item.authors.length > 0 ? item.authors.join(", ") : "Unknown author"}
                </p>
                <div className="library-state">
                  {item.readable_document_id ? (
                    <>
                      <span>{item.reading_status ?? "Ready to read"}</span>
                      <span>{progress}%</span>
                    </>
                  ) : (
                    <span>Saved · readable document not ready yet</span>
                  )}
                </div>
                <div className="library-actions">
                  {item.readable_document_id ? (
                    <a
                      className="primary-action link-button"
                      href={`/read/${item.library_entry_id}/${item.readable_document_id}`}
                    >
                      {progress > 0 ? "Continue reading" : "Read"}
                    </a>
                  ) : null}
                  <a className="secondary-link" href={`/dossier?provider=library&record_id=${item.work_id}`}>
                    Details
                  </a>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

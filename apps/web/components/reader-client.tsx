"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "../lib/api";
import { ReaderResearchPanel } from "./reader-research-panel";

type ReaderLocator = Record<string, string | number>;

type ReaderSection = {
  section_id: string;
  ordinal: number;
  heading: string | null;
  locator: ReaderLocator;
  text: string;
};

type ReadingState = {
  reading_state_id: string;
  library_entry_id: string;
  document_id: string;
  status: string;
  progress_fraction: number;
  section_id: string | null;
  section_ordinal: number | null;
  char_offset: number | null;
  locator: ReaderLocator;
};

type Bookmark = {
  bookmark_id: string;
  section_id: string;
  char_offset: number;
  locator: ReaderLocator;
  label: string | null;
};

type ReaderDocument = {
  library_entry_id: string;
  document_id: string;
  asset_id: string;
  format: string;
  parser_name: string;
  parser_version: string;
  section_count: number;
  chunk_count: number;
  reading_state: ReadingState | null;
  bookmarks: Bookmark[];
  sections: ReaderSection[];
  next_after_ordinal: number | null;
};

type ReaderClientProps = {
  libraryEntryId: string;
  documentId: string;
};

const PAGE_SIZE = 12;

function readerUrl(libraryEntryId: string, documentId: string, after?: number): string {
  const base = `/v1/library/${libraryEntryId}/documents/${documentId}/reader`;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
  if (after !== undefined) params.set("after", String(after));
  return `${base}?${params.toString()}`;
}

function locatorLabel(locator: ReaderLocator): string {
  const priority = ["page", "spine", "section", "paragraph", "row"];
  const parts = priority
    .filter((key) => locator[key] !== undefined)
    .map((key) => `${key} ${String(locator[key])}`);
  return parts.length > 0 ? parts.join(" · ") : "Source coordinate";
}

export function ReaderClient({ libraryEntryId, documentId }: ReaderClientProps) {
  const [document, setDocument] = useState<ReaderDocument | null>(null);
  const [sections, setSections] = useState<ReaderSection[]>([]);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [readingState, setReadingState] = useState<ReadingState | null>(null);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastPersistedSection = useRef<string | null>(null);

  const basePath = useMemo(
    () => `/v1/library/${libraryEntryId}/documents/${documentId}`,
    [libraryEntryId, documentId],
  );

  const fetchPage = useCallback(
    async (after?: number): Promise<ReaderDocument> => {
      const response = await apiFetch(readerUrl(libraryEntryId, documentId, after), {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Reader failed with HTTP ${response.status}.`);
      }
      return (await response.json()) as ReaderDocument;
    },
    [libraryEntryId, documentId],
  );

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const initial = await fetchPage();
        let visible = initial;
        const resumeOrdinal = initial.reading_state?.section_ordinal;
        const containsResume = initial.sections.some(
          (section) => section.ordinal === resumeOrdinal,
        );
        if (
          resumeOrdinal !== null &&
          resumeOrdinal !== undefined &&
          resumeOrdinal > 0 &&
          !containsResume
        ) {
          visible = await fetchPage(resumeOrdinal - 1);
        }
        if (cancelled) return;
        setDocument(visible);
        setSections(visible.sections);
        setBookmarks(visible.bookmarks);
        setReadingState(visible.reading_state);
        setNextAfter(visible.next_after_ordinal);
        const resumeId = visible.reading_state?.section_id;
        setActiveSectionId(resumeId ?? visible.sections[0]?.section_id ?? null);
        if (resumeId) {
          requestAnimationFrame(() => {
            documentElement(resumeId)?.focus({ preventScroll: true });
            documentElement(resumeId)?.scrollIntoView({ block: "start" });
          });
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Reader failed.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [fetchPage]);

  useEffect(() => {
    if (sections.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
        const sectionId = visible[0]?.target.getAttribute("data-reader-section-id");
        if (sectionId) setActiveSectionId(sectionId);
      },
      { rootMargin: "-18% 0px -62% 0px", threshold: [0, 0.25, 0.5] },
    );
    for (const section of sections) {
      const element = documentElement(section.section_id);
      if (element) observer.observe(element);
    }
    return () => observer.disconnect();
  }, [sections]);

  useEffect(() => {
    if (!activeSectionId || !document) return;
    if (lastPersistedSection.current === activeSectionId) return;
    const section = sections.find((item) => item.section_id === activeSectionId);
    if (!section) return;
    const timer = window.setTimeout(async () => {
      const progress = Math.min(1, (section.ordinal + 1) / document.section_count);
      try {
        const response = await apiFetch(`${basePath}/progress`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            section_id: section.section_id,
            char_offset: 0,
            progress_fraction: progress,
          }),
        });
        if (!response.ok) return;
        const persisted = (await response.json()) as ReadingState;
        lastPersistedSection.current = section.section_id;
        setReadingState(persisted);
      } catch {
        // Reading stays usable during a transient persistence failure.
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [activeSectionId, basePath, document, sections]);

  async function loadMore() {
    if (nextAfter === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await fetchPage(nextAfter);
      setSections((current) => {
        const existing = new Set(current.map((section) => section.section_id));
        return [...current, ...page.sections.filter((section) => !existing.has(section.section_id))];
      });
      setBookmarks(page.bookmarks);
      setReadingState(page.reading_state);
      setNextAfter(page.next_after_ordinal);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load more sections.");
    } finally {
      setLoadingMore(false);
    }
  }

  async function toggleBookmark(section: ReaderSection) {
    const existing = bookmarks.find(
      (bookmark) => bookmark.section_id === section.section_id && bookmark.char_offset === 0,
    );
    setError(null);
    try {
      if (existing) {
        const response = await apiFetch(`${basePath}/bookmarks/${existing.bookmark_id}/remove`, {
          method: "POST",
        });
        if (!response.ok) throw new Error(`Bookmark removal failed with HTTP ${response.status}.`);
        setBookmarks((current) =>
          current.filter((bookmark) => bookmark.bookmark_id !== existing.bookmark_id),
        );
        return;
      }
      const response = await apiFetch(`${basePath}/bookmarks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section_id: section.section_id,
          char_offset: 0,
          label: section.heading,
        }),
      });
      if (!response.ok) throw new Error(`Bookmark failed with HTTP ${response.status}.`);
      const created = (await response.json()) as Bookmark;
      setBookmarks((current) => [created, ...current]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Bookmark update failed.");
    }
  }

  if (loading) {
    return (
      <main className="reader-shell" aria-busy="true">
        <p className="reader-kicker">Opening your book…</p>
      </main>
    );
  }

  if (error && !document) {
    return (
      <main className="reader-shell">
        <a className="reader-back" href="/library">← Library</a>
        <div className="error-card" role="alert">{error}</div>
      </main>
    );
  }

  if (!document) return null;

  const progressPercent = Math.round((readingState?.progress_fraction ?? 0) * 100);
  const activeSection =
    sections.find((section) => section.section_id === activeSectionId) ?? sections[0] ?? null;

  return (
    <main className="reader-shell">
      <header className="reader-toolbar">
        <div>
          <a className="reader-back" href="/library">← Library</a>
          <p className="reader-kicker">{document.format} · {document.parser_name}</p>
        </div>
        <div className="reader-progress" aria-label={`Reading progress ${progressPercent}%`}>
          <span>{progressPercent}%</span>
          <div className="reader-progress-track" aria-hidden="true">
            <span style={{ width: `${progressPercent}%` }} />
          </div>
        </div>
      </header>

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      <div className="reader-layout">
        <aside className="reader-sidebar" aria-label="Reading tools">
          <div className="reader-sidebar-card">
            <span className="reader-meta-label">Position</span>
            <strong>{readingState ? `${progressPercent}% complete` : "Not started"}</strong>
            <p>
              {readingState ? locatorLabel(readingState.locator) : "Progress saves as you read."}
            </p>
          </div>
          <div className="reader-sidebar-card">
            <span className="reader-meta-label">Bookmarks</span>
            <strong>{bookmarks.length}</strong>
            <nav aria-label="Bookmarks">
              {bookmarks.length === 0 ? <p>No bookmarks yet.</p> : null}
              {bookmarks.map((bookmark) => (
                <a key={bookmark.bookmark_id} href={`#reader-section-${bookmark.section_id}`}>
                  {bookmark.label ?? locatorLabel(bookmark.locator)}
                </a>
              ))}
            </nav>
          </div>
          <ReaderResearchPanel
            libraryEntryId={libraryEntryId}
            documentId={documentId}
            sectionId={activeSection?.section_id ?? null}
            sectionHeading={activeSection?.heading ?? null}
            sectionLocator={activeSection?.locator ?? null}
          />
        </aside>

        <article className="reader-paper" aria-label="Book text">
          {sections.map((section) => {
            const bookmarked = bookmarks.some(
              (bookmark) =>
                bookmark.section_id === section.section_id && bookmark.char_offset === 0,
            );
            return (
              <section
                className="reader-section"
                id={`reader-section-${section.section_id}`}
                data-reader-section-id={section.section_id}
                key={section.section_id}
                tabIndex={-1}
              >
                <div className="reader-section-meta">
                  <span>{locatorLabel(section.locator)}</span>
                  <button
                    type="button"
                    aria-pressed={bookmarked}
                    onClick={() => void toggleBookmark(section)}
                  >
                    {bookmarked ? "Bookmarked" : "Bookmark"}
                  </button>
                </div>
                {section.heading ? <h2>{section.heading}</h2> : null}
                {section.text.split("\n").filter(Boolean).map((paragraph, index) => (
                  <p key={`${section.section_id}:${index}`}>{paragraph}</p>
                ))}
              </section>
            );
          })}

          {nextAfter !== null ? (
            <button
              className="reader-load-more"
              type="button"
              onClick={() => void loadMore()}
              disabled={loadingMore}
            >
              {loadingMore ? "Loading…" : "Continue reading"}
            </button>
          ) : (
            <p className="reader-end">End of processed text.</p>
          )}
        </article>
      </div>
    </main>
  );
}

function documentElement(sectionId: string): HTMLElement | null {
  return document.getElementById(`reader-section-${sectionId}`);
}

"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "../lib/api";
import { readerPositionHref } from "../lib/reader-links";
import {
  ReaderAnnotations,
  type PendingReaderSelection,
  type ReaderHighlight,
  type ReaderLocator,
} from "./reader-annotations";
import "./reader-annotations.module.css";
import { ReaderFormatNavigation } from "./reader-format-navigation";
import { ReaderResearchPanel } from "./reader-research-panel";

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
  section_ordinal: number;
  char_offset: number;
  locator: ReaderLocator;
  label: string | null;
  updated_at: string;
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
  highlights: ReaderHighlight[];
  sections: ReaderSection[];
  next_after_ordinal: number | null;
};

type ReaderClientProps = {
  libraryEntryId: string;
  documentId: string;
};

type ReaderParagraph = {
  key: number;
  text: string;
  charStart: number;
  charEnd: number;
};

type ReaderPosition = {
  sectionId: string | null;
  charOffset: number;
};

const PAGE_SIZE = 12;
const READING_BAND = { rootMargin: "-18% 0px -62% 0px", threshold: [0, 0.25, 0.5] };

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

function requestedSectionOrdinal(): number | null {
  const raw = new URLSearchParams(window.location.search).get("section");
  if (raw === null || raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function requestedCharOffset(): number | null {
  const raw = new URLSearchParams(window.location.search).get("offset");
  if (raw === null || raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isInteger(value) && value >= 0 ? value : null;
}

export function ReaderClient({ libraryEntryId, documentId }: ReaderClientProps) {
  const [document, setDocument] = useState<ReaderDocument | null>(null);
  const [sections, setSections] = useState<ReaderSection[]>([]);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [highlights, setHighlights] = useState<ReaderHighlight[]>([]);
  const [pendingSelection, setPendingSelection] = useState<PendingReaderSelection | null>(null);
  const [annotationBusyKey, setAnnotationBusyKey] = useState<string | null>(null);
  const [readingState, setReadingState] = useState<ReadingState | null>(null);
  const [nextAfter, setNextAfter] = useState<number | null>(null);
  const [activePosition, setActivePosition] = useState<ReaderPosition>({
    sectionId: null,
    charOffset: 0,
  });
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastPersistedPosition = useRef<string | null>(null);
  const documentEndVisible = useRef(false);
  const pendingScrollPosition = useRef<ReaderPosition | null>(null);

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
        const requestedOrdinal = requestedSectionOrdinal();
        const requestedOffset = requestedCharOffset();
        const initial = await fetchPage(
          requestedOrdinal !== null && requestedOrdinal > 0 ? requestedOrdinal - 1 : undefined,
        );
        let visible = initial;
        let requestedSectionId =
          requestedOrdinal === null
            ? null
            : initial.sections.find((section) => section.ordinal === requestedOrdinal)?.section_id ??
              null;

        if (requestedOrdinal !== null && requestedSectionId === null) {
          visible = await fetchPage();
        }

        if (requestedSectionId === null) {
          const resumeOrdinal = visible.reading_state?.section_ordinal;
          const containsResume = visible.sections.some(
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
        }

        if (cancelled) return;
        if (requestedOrdinal !== null) {
          requestedSectionId =
            visible.sections.find((section) => section.ordinal === requestedOrdinal)?.section_id ??
            requestedSectionId;
        }
        setDocument(visible);
        setSections(visible.sections);
        setBookmarks(visible.bookmarks);
        setHighlights(visible.highlights);
        setReadingState(visible.reading_state);
        setNextAfter(visible.next_after_ordinal);
        const resumeId = visible.reading_state?.section_id;
        const targetId = requestedSectionId ?? resumeId ?? visible.sections[0]?.section_id ?? null;
        const requestedSection =
          requestedSectionId === null
            ? null
            : visible.sections.find((section) => section.section_id === requestedSectionId) ?? null;
        const targetOffset =
          requestedSectionId !== null
            ? Math.min(requestedOffset ?? 0, requestedSection?.text.length ?? 0)
            : targetId !== null && visible.reading_state?.section_id === targetId
              ? (visible.reading_state.char_offset ?? 0)
              : 0;
        const targetPosition = { sectionId: targetId, charOffset: targetOffset };
        pendingScrollPosition.current = targetPosition;
        setActivePosition(targetPosition);
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
    const target = pendingScrollPosition.current;
    if (!target?.sectionId || sections.length === 0) return;
    const sectionElement = documentElement(target.sectionId);
    const positionElement = readerPositionElement(target.sectionId, target.charOffset);
    if (!sectionElement || !positionElement) return;

    sectionElement.focus({ preventScroll: true });
    positionElement.scrollIntoView({ block: "start" });
    setActivePosition(target);
    pendingScrollPosition.current = null;
  }, [sections]);

  useEffect(() => {
    if (sections.length === 0) return;
    documentEndVisible.current = false;

    const paragraphObserver = new IntersectionObserver((entries) => {
      if (documentEndVisible.current) return;
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((left, right) => left.boundingClientRect.top - right.boundingClientRect.top);
      const target = visible[0]?.target;
      if (!(target instanceof HTMLElement)) return;
      const sectionId =
        target.getAttribute("data-reader-section-id") ??
        target.closest("[data-reader-section-id]")?.getAttribute("data-reader-section-id");
      const rawOffset = target.getAttribute("data-reader-char-start");
      const charOffset = rawOffset === null ? 0 : Number(rawOffset);
      if (!sectionId || !Number.isInteger(charOffset) || charOffset < 0) return;
      setActivePosition((current) =>
        current.sectionId === sectionId && current.charOffset === charOffset
          ? current
          : { sectionId, charOffset },
      );
    }, READING_BAND);

    for (const section of sections) {
      const element = documentElement(section.section_id);
      if (!element) continue;
      const paragraphs = element.querySelectorAll("[data-reader-char-start]");
      if (paragraphs.length === 0) {
        paragraphObserver.observe(element);
        continue;
      }
      for (const paragraph of paragraphs) {
        paragraphObserver.observe(paragraph);
      }
    }

    const endObserver = new IntersectionObserver((entries) => {
      const visible = entries.some((entry) => entry.isIntersecting);
      documentEndVisible.current = visible;
      if (!visible || document === null) return;
      const finalSection = sections.find(
        (section) => section.ordinal === document.section_count - 1,
      );
      if (!finalSection) return;
      setActivePosition({
        sectionId: finalSection.section_id,
        charOffset: finalSection.text.length,
      });
    }, READING_BAND);

    if (nextAfter === null) {
      const endElement = documentEndElement();
      if (endElement) endObserver.observe(endElement);
    }

    return () => {
      paragraphObserver.disconnect();
      endObserver.disconnect();
      documentEndVisible.current = false;
    };
  }, [document, nextAfter, sections]);

  useEffect(() => {
    const { sectionId, charOffset } = activePosition;
    if (!sectionId) return;
    const positionKey = `${sectionId}:${charOffset}`;
    if (lastPersistedPosition.current === positionKey) return;
    const section = sections.find((item) => item.section_id === sectionId);
    if (!section) return;
    const timer = window.setTimeout(async () => {
      try {
        const response = await apiFetch(`${basePath}/progress`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            section_id: section.section_id,
            char_offset: charOffset,
          }),
        });
        if (!response.ok) return;
        const persisted = (await response.json()) as ReadingState;
        lastPersistedPosition.current = positionKey;
        setReadingState(persisted);
      } catch {
        // Reading stays usable during a transient persistence failure.
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [activePosition, basePath, sections]);

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
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_updated_at: existing.updated_at,
          }),
        });
        if (response.status === 409) {
          throw new Error("This bookmark changed in another tab. Reload before removing it.");
        }
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

  function captureSelection() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
    const range = selection.getRangeAt(0);
    const startSection = closestElement(range.startContainer, "[data-reader-section-id]");
    const endSection = closestElement(range.endContainer, "[data-reader-section-id]");
    if (!startSection || !endSection || startSection !== endSection) {
      setPendingSelection(null);
      return;
    }
    const sectionId = startSection.getAttribute("data-reader-section-id");
    if (!sectionId) return;
    const section = sections.find((item) => item.section_id === sectionId);
    if (!section) return;
    const charStart = canonicalOffset(range.startContainer, range.startOffset, startSection);
    const charEnd = canonicalOffset(range.endContainer, range.endOffset, startSection);
    if (charStart === null || charEnd === null || charEnd <= charStart) {
      setPendingSelection(null);
      return;
    }
    const text = section.text.slice(charStart, charEnd);
    if (!text.trim()) {
      setPendingSelection(null);
      return;
    }
    setPendingSelection({ sectionId, charStart, charEnd, text });
  }

  async function createHighlight(note: string | null) {
    if (!pendingSelection || annotationBusyKey !== null) return;
    setAnnotationBusyKey("create");
    setError(null);
    try {
      const response = await apiFetch(`${basePath}/highlights`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section_id: pendingSelection.sectionId,
          char_start: pendingSelection.charStart,
          char_end: pendingSelection.charEnd,
          note,
        }),
      });
      if (!response.ok) throw new Error(`Highlight failed with HTTP ${response.status}.`);
      const created = (await response.json()) as ReaderHighlight;
      setHighlights((current) => [
        created,
        ...current.filter((item) => item.highlight_id !== created.highlight_id),
      ]);
      setPendingSelection(null);
      window.getSelection()?.removeAllRanges();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save highlight.");
    } finally {
      setAnnotationBusyKey(null);
    }
  }

  async function updateHighlightNote(highlightId: string, note: string | null) {
    if (annotationBusyKey !== null) return;
    const currentHighlight = highlights.find((highlight) => highlight.highlight_id === highlightId);
    if (!currentHighlight) {
      setError("This highlight is no longer available.");
      return;
    }
    setAnnotationBusyKey(highlightId);
    setError(null);
    try {
      const response = await apiFetch(`${basePath}/highlights/${highlightId}/note`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          note,
          expected_updated_at: currentHighlight.updated_at,
        }),
      });
      if (response.status === 409) {
        throw new Error(
          "This highlight note changed in another tab. Your draft is still here; reload before saving again.",
        );
      }
      if (!response.ok) throw new Error(`Note update failed with HTTP ${response.status}.`);
      const updated = (await response.json()) as ReaderHighlight;
      setHighlights((current) =>
        current.map((item) => (item.highlight_id === updated.highlight_id ? updated : item)),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update note.");
    } finally {
      setAnnotationBusyKey(null);
    }
  }

  async function removeHighlight(highlightId: string) {
    if (annotationBusyKey !== null) return;
    const currentHighlight = highlights.find((highlight) => highlight.highlight_id === highlightId);
    if (!currentHighlight) {
      setError("This highlight is no longer available.");
      return;
    }
    setAnnotationBusyKey(highlightId);
    setError(null);
    try {
      const response = await apiFetch(`${basePath}/highlights/${highlightId}/remove`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          expected_updated_at: currentHighlight.updated_at,
        }),
      });
      if (response.status === 409) {
        throw new Error(
          "This highlight changed in another tab. Reload before removing it.",
        );
      }
      if (!response.ok) throw new Error(`Highlight removal failed with HTTP ${response.status}.`);
      setHighlights((current) =>
        current.filter((item) => item.highlight_id !== highlightId),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not remove highlight.");
    } finally {
      setAnnotationBusyKey(null);
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
    sections.find((section) => section.section_id === activePosition.sectionId) ??
    sections[0] ??
    null;

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
          <ReaderFormatNavigation
            libraryEntryId={libraryEntryId}
            documentId={documentId}
            activeLocator={activeSection?.locator ?? readingState?.locator ?? null}
          />
          <div className="reader-sidebar-card">
            <span className="reader-meta-label">Bookmarks</span>
            <strong>{bookmarks.length}</strong>
            <nav aria-label="Bookmarks">
              {bookmarks.length === 0 ? <p>No bookmarks yet.</p> : null}
              {bookmarks.map((bookmark) => (
                <a
                  key={bookmark.bookmark_id}
                  href={readerPositionHref(bookmark.section_ordinal, bookmark.char_offset)}
                >
                  {bookmark.label ?? locatorLabel(bookmark.locator)}
                </a>
              ))}
            </nav>
          </div>
          <ReaderAnnotations
            highlights={highlights}
            selection={pendingSelection}
            busyKey={annotationBusyKey}
            onCreate={createHighlight}
            onUpdateNote={updateHighlightNote}
            onRemove={removeHighlight}
            onClearSelection={() => setPendingSelection(null)}
          />
          <ReaderResearchPanel
            libraryEntryId={libraryEntryId}
            documentId={documentId}
            sectionId={activeSection?.section_id ?? null}
            sectionHeading={activeSection?.heading ?? null}
            sectionLocator={activeSection?.locator ?? null}
            highlights={highlights}
          />
        </aside>

        <article
          className="reader-paper"
          aria-label="Book text"
          onMouseUp={captureSelection}
          onKeyUp={captureSelection}
        >
          {sections.map((section) => {
            const bookmarked = bookmarks.some(
              (bookmark) =>
                bookmark.section_id === section.section_id && bookmark.char_offset === 0,
            );
            const sectionHighlights = highlights.filter(
              (highlight) => highlight.section_id === section.section_id,
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
                {paragraphsWithOffsets(section.text).map((paragraph) => (
                  <p
                    className="reader-paragraph"
                    data-reader-char-start={paragraph.charStart}
                    key={`${section.section_id}:${paragraph.key}`}
                  >
                    {renderParagraphHighlights(paragraph, sectionHighlights)}
                  </p>
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
            <p className="reader-end" data-reader-document-end="true">
              End of processed text.
            </p>
          )}
        </article>
      </div>
    </main>
  );
}

function paragraphsWithOffsets(text: string): ReaderParagraph[] {
  const lines = text.split("\n");
  const paragraphs: ReaderParagraph[] = [];
  let cursor = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    const charStart = cursor;
    const charEnd = charStart + line.length;
    if (line.length > 0) {
      paragraphs.push({ key: index, text: line, charStart, charEnd });
    }
    cursor = charEnd + (index < lines.length - 1 ? 1 : 0);
  }
  return paragraphs;
}

function renderParagraphHighlights(
  paragraph: ReaderParagraph,
  highlights: ReaderHighlight[],
): ReactNode[] {
  const ranges = highlights
    .map((highlight) => ({
      start: Math.max(paragraph.charStart, highlight.char_start),
      end: Math.min(paragraph.charEnd, highlight.char_end),
    }))
    .filter((range) => range.end > range.start)
    .sort((left, right) => left.start - right.start || left.end - right.end);

  const merged: Array<{ start: number; end: number }> = [];
  for (const range of ranges) {
    const previous = merged[merged.length - 1];
    if (previous && range.start <= previous.end) {
      previous.end = Math.max(previous.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }

  const nodes: ReactNode[] = [];
  let cursor = paragraph.charStart;
  for (const range of merged) {
    if (range.start > cursor) {
      nodes.push(paragraph.text.slice(cursor - paragraph.charStart, range.start - paragraph.charStart));
    }
    nodes.push(
      <mark className="reader-highlight-mark" key={`${range.start}:${range.end}`}>
        {paragraph.text.slice(
          range.start - paragraph.charStart,
          range.end - paragraph.charStart,
        )}
      </mark>,
    );
    cursor = range.end;
  }
  if (cursor < paragraph.charEnd) {
    nodes.push(paragraph.text.slice(cursor - paragraph.charStart));
  }
  return nodes;
}

function canonicalOffset(container: Node, offset: number, section: Element): number | null {
  const paragraph = closestElement(container, "[data-reader-char-start]");
  if (!paragraph || !section.contains(paragraph)) return null;
  const startValue = paragraph.getAttribute("data-reader-char-start");
  if (startValue === null) return null;
  const paragraphStart = Number(startValue);
  if (!Number.isFinite(paragraphStart)) return null;
  try {
    const range = document.createRange();
    range.selectNodeContents(paragraph);
    range.setEnd(container, offset);
    return paragraphStart + range.toString().length;
  } catch {
    return null;
  }
}

function closestElement(node: Node, selector: string): Element | null {
  const element = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
  return element?.closest(selector) ?? null;
}

function documentElement(sectionId: string): HTMLElement | null {
  return document.getElementById(`reader-section-${sectionId}`);
}

function documentEndElement(): HTMLElement | null {
  return document.querySelector<HTMLElement>("[data-reader-document-end]");
}

function readerPositionElement(sectionId: string, charOffset: number): HTMLElement | null {
  const section = documentElement(sectionId);
  if (!section) return null;

  let candidate: HTMLElement = section;
  for (const paragraph of section.querySelectorAll<HTMLElement>("[data-reader-char-start]")) {
    const value = Number(paragraph.getAttribute("data-reader-char-start"));
    if (!Number.isInteger(value) || value < 0) continue;
    if (value > charOffset) break;
    candidate = paragraph;
  }
  return candidate;
}

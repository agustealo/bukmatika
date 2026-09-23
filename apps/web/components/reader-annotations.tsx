"use client";

import { useEffect, useState } from "react";

import {
  MAX_SELECTED_RESEARCH_HIGHLIGHTS,
  publishResearchHighlightSelection,
} from "../lib/research-highlight-selection";

export type ReaderLocator = Record<string, string | number>;

export type ReaderHighlight = {
  highlight_id: string;
  section_id: string;
  char_start: number;
  char_end: number;
  locator: ReaderLocator;
  text: string;
  note: string | null;
};

export type PendingReaderSelection = {
  sectionId: string;
  charStart: number;
  charEnd: number;
  text: string;
};

type ReaderAnnotationsProps = {
  highlights: ReaderHighlight[];
  selection: PendingReaderSelection | null;
  busyKey: string | null;
  onCreate: (note: string | null) => Promise<void>;
  onUpdateNote: (highlightId: string, note: string | null) => Promise<void>;
  onRemove: (highlightId: string) => Promise<void>;
  onClearSelection: () => void;
};

export function ReaderAnnotations({
  highlights,
  selection,
  busyKey,
  onCreate,
  onUpdateNote,
  onRemove,
  onClearSelection,
}: ReaderAnnotationsProps) {
  const [selectionNote, setSelectionNote] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingNote, setEditingNote] = useState("");
  const [researchHighlightIds, setResearchHighlightIds] = useState<string[]>([]);

  useEffect(() => {
    setSelectionNote("");
  }, [selection?.sectionId, selection?.charStart, selection?.charEnd]);

  useEffect(() => {
    const available = new Set(highlights.map((highlight) => highlight.highlight_id));
    setResearchHighlightIds((current) => {
      const next = current.filter((highlightId) => available.has(highlightId));
      if (next.length !== current.length) publishResearchHighlightSelection(next);
      return next;
    });
  }, [highlights]);

  function beginEdit(highlight: ReaderHighlight) {
    setEditingId(highlight.highlight_id);
    setEditingNote(highlight.note ?? "");
  }

  function toggleResearchHighlight(highlightId: string) {
    setResearchHighlightIds((current) => {
      const next = current.includes(highlightId)
        ? current.filter((candidate) => candidate !== highlightId)
        : [...current, highlightId];
      publishResearchHighlightSelection(next);
      return next;
    });
  }

  return (
    <div className="reader-sidebar-card reader-annotation-card">
      <span className="reader-meta-label">Highlights & notes</span>
      <strong>{highlights.length}</strong>
      <p className="reader-annotation-hint">
        Select text inside one section to save an exact coordinate-backed highlight.
      </p>
      {researchHighlightIds.length > 0 ? (
        <p className="reader-annotation-hint" aria-live="polite">
          {researchHighlightIds.length} saved highlight
          {researchHighlightIds.length === 1 ? "" : "s"} pinned to the next research request.
        </p>
      ) : null}

      {selection ? (
        <div className="reader-selection-card" aria-label="Selected text annotation">
          <blockquote>“{selection.text}”</blockquote>
          <textarea
            aria-label="Optional note for selected text"
            maxLength={4000}
            placeholder="Add a note…"
            value={selectionNote}
            onChange={(event) => setSelectionNote(event.target.value)}
          />
          <div className="reader-annotation-actions">
            <button
              type="button"
              data-primary="true"
              disabled={busyKey === "create"}
              onClick={() => void onCreate(selectionNote.trim() || null)}
            >
              {busyKey === "create" ? "Saving…" : "Save highlight"}
            </button>
            <button type="button" onClick={onClearSelection}>Cancel</button>
          </div>
        </div>
      ) : null}

      <div className="reader-highlight-list">
        {highlights.length === 0 ? <p>No highlights yet.</p> : null}
        {highlights.map((highlight) => {
          const editing = editingId === highlight.highlight_id;
          const busy = busyKey === highlight.highlight_id;
          const selectedForResearch = researchHighlightIds.includes(highlight.highlight_id);
          const researchSelectionFull =
            researchHighlightIds.length >= MAX_SELECTED_RESEARCH_HIGHLIGHTS && !selectedForResearch;
          return (
            <article className="reader-highlight-item" key={highlight.highlight_id}>
              <a href={`#reader-section-${highlight.section_id}`}>Open passage</a>
              <blockquote>“{highlight.text}”</blockquote>
              {editing ? (
                <textarea
                  aria-label="Highlight note"
                  maxLength={4000}
                  value={editingNote}
                  onChange={(event) => setEditingNote(event.target.value)}
                />
              ) : highlight.note ? (
                <p className="reader-highlight-note">{highlight.note}</p>
              ) : null}
              <div className="reader-annotation-actions">
                <button
                  type="button"
                  aria-pressed={selectedForResearch}
                  disabled={researchSelectionFull}
                  onClick={() => toggleResearchHighlight(highlight.highlight_id)}
                >
                  {selectedForResearch ? "Remove from research" : "Use in research"}
                </button>
                {editing ? (
                  <>
                    <button
                      type="button"
                      data-primary="true"
                      disabled={busy}
                      onClick={() => {
                        void onUpdateNote(
                          highlight.highlight_id,
                          editingNote.trim() || null,
                        ).then(() => setEditingId(null));
                      }}
                    >
                      {busy ? "Saving…" : "Save note"}
                    </button>
                    <button type="button" onClick={() => setEditingId(null)}>Cancel</button>
                  </>
                ) : (
                  <button type="button" onClick={() => beginEdit(highlight)}>
                    {highlight.note ? "Edit note" : "Add note"}
                  </button>
                )}
                <button
                  type="button"
                  data-danger="true"
                  disabled={busy}
                  onClick={() => void onRemove(highlight.highlight_id)}
                >
                  Remove
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

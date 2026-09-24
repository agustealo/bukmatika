"use client";

import { useEffect, useState } from "react";

import { readerPositionHref } from "../lib/reader-links";

export type ReaderLocator = Record<string, string | number>;

export type ReaderHighlight = {
  highlight_id: string;
  section_id: string;
  section_ordinal: number;
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

  useEffect(() => {
    setSelectionNote("");
  }, [selection?.sectionId, selection?.charStart, selection?.charEnd]);

  function beginEdit(highlight: ReaderHighlight) {
    setEditingId(highlight.highlight_id);
    setEditingNote(highlight.note ?? "");
  }

  return (
    <div className="reader-sidebar-card reader-annotation-card">
      <span className="reader-meta-label">Highlights & notes</span>
      <strong>{highlights.length}</strong>
      <p className="reader-annotation-hint">
        Select text inside one section to save an exact coordinate-backed highlight.
      </p>

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
          return (
            <article className="reader-highlight-item" key={highlight.highlight_id}>
              <a href={readerPositionHref(highlight.section_ordinal, highlight.char_start)}>
                Open passage
              </a>
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

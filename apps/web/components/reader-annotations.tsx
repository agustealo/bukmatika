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
  updated_at: string;
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
  onRemove: (highlightId: string, expectedUpdatedAt: string) => Promise<void>;
  onClearSelection: () => void;
};

type PendingNoteSave = {
  highlightId: string;
  note: string | null;
  sawBusy: boolean;
};

function normalizedNote(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed || null;
}

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
  const [pendingNoteSave, setPendingNoteSave] = useState<PendingNoteSave | null>(null);
  const annotationsBusy = busyKey !== null;

  useEffect(() => {
    setSelectionNote("");
  }, [selection?.sectionId, selection?.charStart, selection?.charEnd]);

  useEffect(() => {
    if (!pendingNoteSave) return;
    if (!pendingNoteSave.sawBusy) {
      if (busyKey === pendingNoteSave.highlightId) {
        setPendingNoteSave((current) =>
          current && current.highlightId === pendingNoteSave.highlightId
            ? { ...current, sawBusy: true }
            : current,
        );
      }
      return;
    }
    if (busyKey === pendingNoteSave.highlightId) return;
    const persisted = highlights.find(
      (highlight) => highlight.highlight_id === pendingNoteSave.highlightId,
    );
    if (persisted && normalizedNote(persisted.note) === pendingNoteSave.note) {
      setEditingId(null);
    }
    setPendingNoteSave(null);
  }, [busyKey, highlights, pendingNoteSave]);

  function beginEdit(highlight: ReaderHighlight) {
    setPendingNoteSave(null);
    setEditingId(highlight.highlight_id);
    setEditingNote(highlight.note ?? "");
  }

  function cancelEdit() {
    setPendingNoteSave(null);
    setEditingId(null);
  }

  function saveNote(highlight: ReaderHighlight) {
    if (annotationsBusy) return;
    const requestedNote = normalizedNote(editingNote);
    if (requestedNote === normalizedNote(highlight.note)) {
      setEditingId(null);
      return;
    }
    setPendingNoteSave({
      highlightId: highlight.highlight_id,
      note: requestedNote,
      sawBusy: false,
    });
    void onUpdateNote(highlight.highlight_id, requestedNote);
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
            disabled={annotationsBusy}
            onChange={(event) => setSelectionNote(event.target.value)}
          />
          <div className="reader-annotation-actions">
            <button
              type="button"
              data-primary="true"
              disabled={annotationsBusy}
              onClick={() => void onCreate(selectionNote.trim() || null)}
            >
              {busyKey === "create" ? "Saving…" : "Save highlight"}
            </button>
            <button type="button" disabled={annotationsBusy} onClick={onClearSelection}>
              Cancel
            </button>
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
                  disabled={annotationsBusy}
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
                      disabled={annotationsBusy}
                      onClick={() => saveNote(highlight)}
                    >
                      {busy ? "Saving…" : "Save note"}
                    </button>
                    <button type="button" disabled={annotationsBusy} onClick={cancelEdit}>
                      Cancel
                    </button>
                  </>
                ) : (
                  <button type="button" disabled={annotationsBusy} onClick={() => beginEdit(highlight)}>
                    {highlight.note ? "Edit note" : "Add note"}
                  </button>
                )}
                <button
                  type="button"
                  data-danger="true"
                  disabled={annotationsBusy}
                  onClick={() => void onRemove(highlight.highlight_id, highlight.updated_at)}
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

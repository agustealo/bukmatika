"use client";

import { FormEvent, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./reader-research-panel.module.css";

type ReaderLocator = Record<string, string | number>;

type ReaderResearchPanelProps = {
  libraryEntryId: string;
  documentId: string;
  sectionId: string | null;
  sectionHeading: string | null;
  sectionLocator: ReaderLocator | null;
};

type EvidenceItem = {
  evidence_id: string;
  source_kind: "reader_position" | "reader_selection" | "related_passage";
  library_entry_id: string;
  work_title: string;
  edition_title: string;
  document_id: string;
  chunk_id: string;
  section_id: string;
  heading: string | null;
  locator: ReaderLocator;
  char_start: number;
  char_end: number;
  text: string;
  score: number | null;
};

type EvidenceBundle = {
  question: string;
  selected_library_entry_ids: string[];
  evidence: EvidenceItem[];
};

function locatorLabel(locator: ReaderLocator): string {
  const priority = ["page", "spine", "section", "paragraph", "row"];
  const parts = priority
    .filter((key) => locator[key] !== undefined)
    .map((key) => `${key} ${String(locator[key])}`);
  return parts.length > 0 ? parts.join(" · ") : "Source coordinate";
}

function excerpt(text: string): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > 260 ? `${normalized.slice(0, 257)}…` : normalized;
}

export function ReaderResearchPanel({
  libraryEntryId,
  documentId,
  sectionId,
  sectionHeading,
  sectionLocator,
}: ReaderResearchPanelProps) {
  const [question, setQuestion] = useState("");
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function buildEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    if (!sectionId || !normalized) return;

    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/v1/research/evidence", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: normalized,
          reader: {
            library_entry_id: libraryEntryId,
            document_id: documentId,
            section_id: sectionId,
            char_offset: 0,
          },
          library_entry_ids: [libraryEntryId],
          related_limit: 6,
        }),
      });
      if (!response.ok) {
        throw new Error(`Evidence grounding failed with HTTP ${response.status}.`);
      }
      setBundle((await response.json()) as EvidenceBundle);
    } catch (caught) {
      setBundle(null);
      setError(caught instanceof Error ? caught.message : "Could not ground this question.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={styles.panel}>
      <span className={styles.eyebrow}>Research this passage</span>
      <strong>{sectionHeading ?? "Current section"}</strong>
      <p className={styles.position}>
        {sectionLocator ? locatorLabel(sectionLocator) : "Canonical reader position"}
      </p>
      <p className={styles.explanation}>
        Build a source bundle from this passage and related text in the selected book. Bukmatika is
        not generating an answer yet.
      </p>

      <form className={styles.form} onSubmit={(event) => void buildEvidence(event)}>
        <label htmlFor="reader-research-question">Research question</label>
        <textarea
          id="reader-research-question"
          value={question}
          maxLength={500}
          rows={3}
          disabled={loading || sectionId === null}
          placeholder="What does this passage say about…?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <button type="submit" disabled={loading || sectionId === null || !question.trim()}>
          {loading ? "Grounding…" : "Build evidence"}
        </button>
      </form>

      {error ? <p className={styles.error} role="alert">{error}</p> : null}

      {bundle ? (
        <div className={styles.results} aria-live="polite">
          <div className={styles.resultsHeading}>
            <span>Grounded evidence</span>
            <strong>{bundle.evidence.length}</strong>
          </div>
          <ol>
            {bundle.evidence.map((item) => (
              <li key={item.evidence_id}>
                <div className={styles.evidenceTopline}>
                  <code>{item.evidence_id}</code>
                  <span>{item.source_kind.replaceAll("_", " ")}</span>
                </div>
                <strong>{item.heading ?? item.work_title}</strong>
                <small>{locatorLabel(item.locator)}</small>
                <p>{excerpt(item.text)}</p>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

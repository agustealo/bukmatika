"use client";

import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./research-grounded-answer.module.css";

type EvidenceItem = {
  evidence_id: string;
  source_kind: string;
  library_entry_id: string;
  work_title: string;
  edition_title: string;
  document_id: string;
  section_id: string;
  section_ordinal: number;
  heading: string | null;
  char_start: number;
  char_end: number;
  text: string;
};

type EvidenceBundle = {
  question: string;
  selected_library_entry_ids: string[];
  evidence: EvidenceItem[];
};

type GroundedClaim = {
  text: string;
  evidence_ids: string[];
};

type GroundedResearchResponse = {
  evidence: EvidenceBundle;
  answer: { claims: GroundedClaim[] };
  model_provider: string;
  model_name: string;
  model_routing: string;
};

type ResearchGroundedAnswerProps = {
  question: string;
  libraryEntryIds: string[];
  hasEvidence: boolean;
};

function evidenceHref(item: EvidenceItem): string {
  const params = new URLSearchParams({
    section: String(item.section_ordinal),
    offset: String(item.char_start),
  });
  return `/read/${item.library_entry_id}/${item.document_id}?${params.toString()}#reader-section-${item.section_id}`;
}

function citationLabel(evidenceIds: string[]): string {
  return evidenceIds.join(", ");
}

async function responseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      "code" in payload.detail &&
      typeof payload.detail.code === "string"
    ) {
      const code = payload.detail.code;
      if (code === "RESEARCH_SELECTION_EVIDENCE_UNAVAILABLE") {
        return new Error("No canonical lexical evidence could ground an answer in these books.");
      }
      if (code === "AI_DISABLED") {
        return new Error("AI is disabled. Passage search remains available without AI.");
      }
      if (code === "MODEL_PROVIDER_UNCONFIGURED") {
        return new Error("Configure a local model before requesting a grounded answer.");
      }
      if (code === "MODEL_PROVIDER_NOT_READY") {
        return new Error("The configured local model is not ready yet.");
      }
      return new Error(code.replaceAll("_", " ").toLowerCase());
    }
  } catch {
    // Fall through to the HTTP-aware message.
  }
  return new Error(`Grounded answer failed with HTTP ${response.status}.`);
}

export function ResearchGroundedAnswer({
  question,
  libraryEntryIds,
  hasEvidence,
}: ResearchGroundedAnswerProps) {
  const [result, setResult] = useState<GroundedResearchResponse | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scopeKey = `${question}\n${libraryEntryIds.join(",")}`;
  useEffect(() => {
    setResult(null);
    setError(null);
  }, [scopeKey]);

  const evidenceById = useMemo(
    () => new Map(result?.evidence.evidence.map((item) => [item.evidence_id, item]) ?? []),
    [result],
  );

  async function synthesize() {
    if (!hasEvidence || running) return;
    setRunning(true);
    setError(null);
    try {
      const response = await apiFetch("/v1/ai/research/answer-selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          library_entry_ids: libraryEntryIds,
          related_limit: 12,
        }),
      });
      if (!response.ok) throw await responseError(response);
      setResult((await response.json()) as GroundedResearchResponse);
    } catch (caught) {
      setResult(null);
      setError(caught instanceof Error ? caught.message : "Grounded answer failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className={styles.surface} aria-labelledby="grounded-answer-heading">
      <div className={styles.heading}>
        <div>
          <span>Grounded synthesis</span>
          <h2 id="grounded-answer-heading">Answer from the selected books</h2>
        </div>
        <button type="button" disabled={!hasEvidence || running} onClick={() => void synthesize()}>
          {running ? "Synthesizing…" : result ? "Regenerate answer" : "Synthesize grounded answer"}
        </button>
      </div>
      <p className={styles.note}>
        Bukmatika independently rebuilds a bounded canonical evidence bundle on the server. The
        browser does not send passage text or choose evidence IDs.
      </p>

      {!hasEvidence ? (
        <p className={styles.unavailable}>Search must return canonical evidence before synthesis.</p>
      ) : null}
      {error ? <p className={styles.error} role="alert">{error}</p> : null}

      {result ? (
        <div className={styles.answer} aria-live="polite">
          <div className={styles.modelMeta}>
            <span>{result.model_provider}</span>
            <span>{result.model_name}</span>
            <span>{result.model_routing}</span>
          </div>
          <ol className={styles.claims}>
            {result.answer.claims.map((claim, index) => (
              <li key={`${index}:${claim.text}`}>
                <p>{claim.text}</p>
                <div className={styles.citations} aria-label="Claim evidence">
                  {claim.evidence_ids.map((evidenceId) => {
                    const evidence = evidenceById.get(evidenceId);
                    return evidence ? (
                      <a key={evidenceId} href={`#research-answer-evidence-${evidenceId}`}>
                        {evidenceId}
                      </a>
                    ) : (
                      <span key={evidenceId}>{evidenceId}</span>
                    );
                  })}
                </div>
              </li>
            ))}
          </ol>

          <div className={styles.evidence}>
            <div className={styles.evidenceHeading}>
              <strong>Canonical evidence</strong>
              <span>{result.evidence.evidence.length} item{result.evidence.evidence.length === 1 ? "" : "s"}</span>
            </div>
            {result.evidence.evidence.map((item) => (
              <article id={`research-answer-evidence-${item.evidence_id}`} key={item.evidence_id}>
                <div className={styles.evidenceMeta}>
                  <strong>{item.evidence_id}</strong>
                  <span>{item.work_title}</span>
                  <span>{item.edition_title}</span>
                </div>
                {item.heading ? <h3>{item.heading}</h3> : null}
                <p>{item.text}</p>
                <div className={styles.evidenceActions}>
                  <span>
                    {item.source_kind} · offsets {item.char_start}–{item.char_end}
                  </span>
                  <a href={evidenceHref(item)}>Open source</a>
                </div>
              </article>
            ))}
          </div>
          <p className={styles.srSummary}>
            Citations used: {citationLabel(result.answer.claims.flatMap((claim) => claim.evidence_ids))}
          </p>
        </div>
      ) : null}
    </section>
  );
}

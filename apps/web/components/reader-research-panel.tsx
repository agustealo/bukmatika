"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

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

type GroundedClaim = {
  text: string;
  evidence_ids: string[];
};

type GroundedAnswer = {
  claims: GroundedClaim[];
};

type GroundedResearchResponse = {
  plan_id: string;
  action_decision_id: string;
  evidence: EvidenceBundle;
  answer: GroundedAnswer;
  model_provider: string;
  model_name: string;
  model_routing: string;
};

type AIAvailabilityState =
  | "ai_disabled"
  | "unconfigured"
  | "provider_unreachable"
  | "provider_invalid"
  | "model_missing"
  | "ready";

type AIStatus = {
  configured: boolean;
  ready: boolean;
  ai_enabled: boolean;
  state: AIAvailabilityState;
  provider: string | null;
  model: string | null;
  routing: string | null;
};

type AIErrorPayload = {
  detail?: {
    code?: string;
    state?: string;
  };
};

const AI_AVAILABILITY_STATES = new Set<AIAvailabilityState>([
  "ai_disabled",
  "unconfigured",
  "provider_unreachable",
  "provider_invalid",
  "model_missing",
  "ready",
]);

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

function isAIAvailabilityState(value: unknown): value is AIAvailabilityState {
  return typeof value === "string" && AI_AVAILABILITY_STATES.has(value as AIAvailabilityState);
}

async function readinessFallbackState(response: Response): Promise<AIAvailabilityState | null> {
  if (response.status !== 503) return null;

  try {
    const payload = (await response.clone().json()) as AIErrorPayload;
    if (payload.detail?.code === "MODEL_PROVIDER_UNCONFIGURED") {
      return "unconfigured";
    }
    if (
      payload.detail?.code === "MODEL_PROVIDER_NOT_READY" &&
      isAIAvailabilityState(payload.detail.state) &&
      payload.detail.state !== "ready" &&
      payload.detail.state !== "ai_disabled"
    ) {
      return payload.detail.state;
    }
  } catch {
    return null;
  }
  return null;
}

function statusExplanation(status: AIStatus | null): string {
  if (status === null) {
    return "Build a canonical source bundle while local AI readiness is being checked.";
  }
  switch (status.state) {
    case "ready":
      return "Ask the ready local model. Every returned claim must cite the canonical evidence shown below.";
    case "ai_disabled":
      return "AI is disabled. Canonical evidence building remains available without model calls.";
    case "unconfigured":
      return "Local AI is not configured. Canonical evidence building remains fully available.";
    case "provider_unreachable":
      return "The local AI runtime is not reachable right now. Evidence building remains available.";
    case "provider_invalid":
      return "The local AI runtime returned an unexpected readiness response. Evidence building remains available.";
    case "model_missing":
      return "The configured local model is not installed. Evidence building remains available.";
  }
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
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null);
  const [answerModel, setAnswerModel] = useState<string | null>(null);
  const [aiStatus, setAIStatus] = useState<AIStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const activeSectionRef = useRef(sectionId);

  useEffect(() => {
    let cancelled = false;

    async function loadAIStatus() {
      try {
        const response = await apiFetch("/v1/ai/status");
        if (!response.ok) return;
        const payload = (await response.json()) as AIStatus;
        if (!cancelled) setAIStatus(payload);
      } catch {
        // Evidence building remains usable when local AI status cannot be loaded.
      }
    }

    void loadAIStatus();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    activeSectionRef.current = sectionId;
    setBundle(null);
    setAnswer(null);
    setAnswerModel(null);
    setError(null);
  }, [sectionId]);

  const canSynthesize = aiStatus?.ready === true && aiStatus.ai_enabled === true;

  async function research(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    const requestedSectionId = sectionId;
    if (!requestedSectionId || !normalized) return;

    const requestBody = {
      question: normalized,
      reader: {
        library_entry_id: libraryEntryId,
        document_id: documentId,
        section_id: requestedSectionId,
        char_offset: 0,
      },
      library_entry_ids: [libraryEntryId],
      related_limit: 6,
    };
    const requestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    };

    setLoading(true);
    setError(null);
    setAnswer(null);
    setAnswerModel(null);
    try {
      let usedSynthesis = canSynthesize;
      let response = await apiFetch(
        usedSynthesis ? "/v1/ai/research/answer" : "/v1/research/evidence",
        requestInit,
      );

      if (usedSynthesis && !response.ok) {
        const fallbackState = await readinessFallbackState(response);
        if (fallbackState !== null) {
          setAIStatus((current) => ({
            configured: fallbackState !== "unconfigured",
            ready: false,
            ai_enabled: true,
            state: fallbackState,
            provider: fallbackState === "unconfigured" ? null : (current?.provider ?? null),
            model: fallbackState === "unconfigured" ? null : (current?.model ?? null),
            routing: fallbackState === "unconfigured" ? null : (current?.routing ?? null),
          }));
          usedSynthesis = false;
          response = await apiFetch("/v1/research/evidence", requestInit);
        }
      }

      if (!response.ok) {
        throw new Error(
          `${usedSynthesis ? "Grounded answer" : "Evidence grounding"} failed with HTTP ${response.status}.`,
        );
      }

      if (usedSynthesis) {
        const payload = (await response.json()) as GroundedResearchResponse;
        if (activeSectionRef.current === requestedSectionId) {
          setBundle(payload.evidence);
          setAnswer(payload.answer);
          setAnswerModel(
            `${payload.model_provider} · ${payload.model_name} · ${payload.model_routing}`,
          );
        }
      } else {
        const payload = (await response.json()) as EvidenceBundle;
        if (activeSectionRef.current === requestedSectionId) {
          setBundle(payload);
        }
      }
    } catch (caught) {
      if (activeSectionRef.current === requestedSectionId) {
        setBundle(null);
        setAnswer(null);
        setAnswerModel(null);
        setError(caught instanceof Error ? caught.message : "Could not research this passage.");
      }
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
      <p className={styles.explanation}>{statusExplanation(aiStatus)}</p>

      <form className={styles.form} onSubmit={(event) => void research(event)}>
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
          {loading ? "Researching…" : canSynthesize ? "Ask local AI" : "Build evidence"}
        </button>
      </form>

      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}

      {answer ? (
        <section className={styles.answer} aria-live="polite">
          <div className={styles.answerHeading}>
            <span>Grounded answer</span>
            {answerModel ? <small>{answerModel}</small> : null}
          </div>
          <ol className={styles.claims}>
            {answer.claims.map((claim, index) => (
              <li key={`${index}-${claim.evidence_ids.join("-")}`}>
                <p>{claim.text}</p>
                <div className={styles.citations} aria-label="Evidence citations">
                  {claim.evidence_ids.map((evidenceId) => (
                    <code key={evidenceId}>{evidenceId}</code>
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </section>
      ) : null}

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

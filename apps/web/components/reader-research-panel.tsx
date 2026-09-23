"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { apiFetch } from "../lib/api";
import type { ReaderHighlight } from "./reader-annotations";
import styles from "./reader-research-panel.module.css";

type ReaderLocator = Record<string, string | number>;

type ReaderResearchPanelProps = {
  libraryEntryId: string;
  documentId: string;
  sectionId: string | null;
  sectionHeading: string | null;
  sectionLocator: ReaderLocator | null;
  highlights: ReaderHighlight[];
};

type EvidenceItem = {
  evidence_id: string;
  source_kind:
    | "reader_position"
    | "reader_selection"
    | "selected_highlight"
    | "related_passage";
  source_highlight_id: string | null;
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

type TimelinePrecision = "day" | "month" | "year" | "ambiguous";

type TimelineItem = {
  timeline_id: string;
  date_label: string;
  year: number;
  month: number | null;
  day: number | null;
  era: "BCE" | "CE" | null;
  precision: TimelinePrecision;
  event_text: string;
  evidence_ids: string[];
  document_id: string;
  section_id: string;
  source_char_start: number;
  source_char_end: number;
};

type TimelineResponse = {
  evidence: EvidenceBundle;
  items: TimelineItem[];
  undated_evidence_ids: string[];
  truncated: boolean;
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

type ActiveAction = "research" | "timeline" | null;

const MAX_SELECTED_HIGHLIGHTS = 8;
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

function excerpt(text: string, maxLength = 260): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength
    ? `${normalized.slice(0, Math.max(0, maxLength - 3))}…`
    : normalized;
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
    return "Build canonical evidence and timelines while local AI readiness is being checked.";
  }
  switch (status.state) {
    case "ready":
      return "Ask the ready local model, or build a deterministic timeline directly from canonical evidence.";
    case "ai_disabled":
      return "AI is disabled. Canonical evidence and deterministic timelines remain available.";
    case "unconfigured":
      return "Local AI is not configured. Evidence and deterministic timelines remain available.";
    case "provider_unreachable":
      return "The local AI runtime is not reachable. Evidence and deterministic timelines remain available.";
    case "provider_invalid":
      return "The local AI runtime returned an unexpected readiness response. Evidence and timelines remain available.";
    case "model_missing":
      return "The configured local model is not installed. Evidence and deterministic timelines remain available.";
  }
}

export function ReaderResearchPanel({
  libraryEntryId,
  documentId,
  sectionId,
  sectionHeading,
  sectionLocator,
  highlights,
}: ReaderResearchPanelProps) {
  const [question, setQuestion] = useState("");
  const [selectedHighlightIds, setSelectedHighlightIds] = useState<string[]>([]);
  const [bundle, setBundle] = useState<EvidenceBundle | null>(null);
  const [answer, setAnswer] = useState<GroundedAnswer | null>(null);
  const [answerModel, setAnswerModel] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [aiStatus, setAIStatus] = useState<AIStatus | null>(null);
  const [activeAction, setActiveAction] = useState<ActiveAction>(null);
  const [error, setError] = useState<string | null>(null);
  const activeSectionRef = useRef(sectionId);
  const loading = activeAction !== null;

  useEffect(() => {
    let cancelled = false;

    async function loadAIStatus() {
      try {
        const response = await apiFetch("/v1/ai/status");
        if (!response.ok) return;
        const payload = (await response.json()) as AIStatus;
        if (!cancelled) setAIStatus(payload);
      } catch {
        // Evidence and deterministic timeline building remain usable without AI status.
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
    setTimeline(null);
    setError(null);
  }, [sectionId]);

  useEffect(() => {
    const available = new Set(highlights.map((highlight) => highlight.highlight_id));
    setSelectedHighlightIds((current) => current.filter((id) => available.has(id)));
  }, [highlights]);

  useEffect(() => {
    setBundle(null);
    setAnswer(null);
    setAnswerModel(null);
    setTimeline(null);
    setError(null);
  }, [selectedHighlightIds]);

  const canSynthesize = aiStatus?.ready === true && aiStatus.ai_enabled === true;

  function toggleHighlight(highlightId: string) {
    setSelectedHighlightIds((current) => {
      if (current.includes(highlightId)) {
        return current.filter((id) => id !== highlightId);
      }
      if (current.length >= MAX_SELECTED_HIGHLIGHTS) return current;
      return [...current, highlightId];
    });
  }

  function requestBody(requestedSectionId: string, normalizedQuestion: string) {
    return {
      question: normalizedQuestion,
      reader: {
        library_entry_id: libraryEntryId,
        document_id: documentId,
        section_id: requestedSectionId,
        char_offset: 0,
      },
      library_entry_ids: [libraryEntryId],
      selected_highlight_ids: selectedHighlightIds,
      related_limit: 6,
    };
  }

  async function research(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = question.trim();
    const requestedSectionId = sectionId;
    if (!requestedSectionId || !normalized || loading) return;

    const requestInit = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody(requestedSectionId, normalized)),
    };

    setActiveAction("research");
    setError(null);
    setBundle(null);
    setAnswer(null);
    setAnswerModel(null);
    setTimeline(null);
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
      setActiveAction(null);
    }
  }

  async function buildTimeline() {
    const normalized = question.trim();
    const requestedSectionId = sectionId;
    if (!requestedSectionId || !normalized || loading) return;

    setActiveAction("timeline");
    setError(null);
    setBundle(null);
    setAnswer(null);
    setAnswerModel(null);
    setTimeline(null);
    try {
      const response = await apiFetch("/v1/research/timeline", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody(requestedSectionId, normalized)),
      });
      if (!response.ok) {
        throw new Error(`Timeline grounding failed with HTTP ${response.status}.`);
      }
      const payload = (await response.json()) as TimelineResponse;
      if (activeSectionRef.current === requestedSectionId) {
        setTimeline(payload);
        setBundle(payload.evidence);
      }
    } catch (caught) {
      if (activeSectionRef.current === requestedSectionId) {
        setTimeline(null);
        setBundle(null);
        setError(caught instanceof Error ? caught.message : "Could not build this timeline.");
      }
    } finally {
      setActiveAction(null);
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
        {highlights.length > 0 ? (
          <fieldset className={styles.highlightPicker}>
            <legend>Include saved highlights</legend>
            <p>
              Only checked highlights are added to this request. They are resolved from saved
              coordinates and are not promoted into AI memory.
            </p>
            <div className={styles.highlightOptions}>
              {highlights.map((highlight) => {
                const checked = selectedHighlightIds.includes(highlight.highlight_id);
                const atLimit = selectedHighlightIds.length >= MAX_SELECTED_HIGHLIGHTS;
                return (
                  <label key={highlight.highlight_id}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={loading || (!checked && atLimit)}
                      onChange={() => toggleHighlight(highlight.highlight_id)}
                    />
                    <span>
                      <strong>{locatorLabel(highlight.locator)}</strong>
                      <small>{excerpt(highlight.text, 120)}</small>
                    </span>
                  </label>
                );
              })}
            </div>
            <small className={styles.highlightCount}>
              {selectedHighlightIds.length}/{MAX_SELECTED_HIGHLIGHTS} selected
            </small>
          </fieldset>
        ) : null}

        <label htmlFor="reader-research-question">Research question</label>
        <textarea
          id="reader-research-question"
          value={question}
          maxLength={500}
          rows={3}
          disabled={loading || sectionId === null}
          placeholder="What happened, and when?"
          onChange={(event) => setQuestion(event.target.value)}
        />
        <div className={styles.actions}>
          <button type="submit" disabled={loading || sectionId === null || !question.trim()}>
            {activeAction === "research"
              ? "Researching…"
              : canSynthesize
                ? "Ask local AI"
                : "Build evidence"}
          </button>
          <button
            type="button"
            disabled={loading || sectionId === null || !question.trim()}
            onClick={() => void buildTimeline()}
          >
            {activeAction === "timeline" ? "Building timeline…" : "Build timeline"}
          </button>
        </div>
      </form>

      {error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : null}

      {timeline ? (
        <section className={styles.timeline} aria-live="polite">
          <div className={styles.answerHeading}>
            <span>Evidence timeline</span>
            <small>Deterministic date extraction. No model call or timeline datastore.</small>
          </div>
          {timeline.items.length === 0 ? (
            <div className={styles.timelineNotice}>
              <strong>No supported explicit dates found.</strong>
              <p>The evidence stays visible below instead of inventing chronology.</p>
            </div>
          ) : (
            <ol className={styles.timelineList}>
              {timeline.items.map((item) => (
                <li className={styles.timelineItem} key={item.timeline_id}>
                  <div className={styles.timelineDate}>
                    <strong>{item.date_label}</strong>
                    <span className={styles.timelinePrecision}>
                      {item.precision === "ambiguous"
                        ? "Ambiguous numeric date"
                        : `${item.precision} precision`}
                    </span>
                  </div>
                  <p>{item.event_text}</p>
                  <div className={styles.timelineMeta}>
                    <span>
                      Text offsets {item.source_char_start}–{item.source_char_end}
                    </span>
                    <div className={styles.citations} aria-label="Timeline evidence citations">
                      {item.evidence_ids.map((evidenceId) => (
                        <code key={evidenceId}>{evidenceId}</code>
                      ))}
                    </div>
                  </div>
                </li>
              ))}
            </ol>
          )}
          {timeline.undated_evidence_ids.length > 0 ? (
            <p className={styles.timelineFootnote}>
              {timeline.undated_evidence_ids.length} evidence item
              {timeline.undated_evidence_ids.length === 1 ? " has" : "s have"} no supported
              explicit date.
            </p>
          ) : null}
          {timeline.truncated ? (
            <p className={styles.timelineFootnote}>
              Timeline output reached its bounded item limit. Narrow the question or evidence to
              inspect the remaining date mentions.
            </p>
          ) : null}
        </section>
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

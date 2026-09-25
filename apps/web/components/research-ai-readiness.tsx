"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./research-ai-readiness.module.css";

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

type ReadinessCopy = {
  title: string;
  message: string;
};

function readinessCopy(status: AIStatus): ReadinessCopy {
  switch (status.state) {
    case "ai_disabled":
      return {
        title: "AI research is off",
        message:
          "Passage search, comparisons, citations, and source reading still work normally. Enable AI assistance before using grounded synthesis or Level 2 planning.",
      };
    case "unconfigured":
      return {
        title: "Choose a local model",
        message:
          "AI research needs an installed local model for this profile. Select one in AI setup before requesting synthesis or a delegated research plan.",
      };
    case "provider_unreachable":
      return {
        title: "Local AI runtime is offline",
        message:
          "Bukmatika cannot currently reach the installation-controlled Ollama runtime. Ordinary evidence research remains available while the runtime is offline.",
      };
    case "provider_invalid":
      return {
        title: "Local AI runtime needs attention",
        message:
          "The configured loopback runtime responded, but it did not satisfy Bukmatika's expected Ollama contract. Check AI setup before using model-backed research.",
      };
    case "model_missing":
      return {
        title: "Selected local model is missing",
        message: status.model
          ? `${status.model} is selected for this profile but is not installed in the current Ollama runtime.`
          : "The model selected for this profile is not installed in the current Ollama runtime.",
      };
    case "ready":
      return {
        title: "Local AI is ready",
        message: "The configured local model is ready for bounded model-backed research.",
      };
  }
}

async function responseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return new Error(payload.detail);
    }
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      "code" in payload.detail &&
      typeof payload.detail.code === "string"
    ) {
      return new Error(payload.detail.code.replaceAll("_", " ").toLowerCase());
    }
  } catch {
    // Keep the HTTP-aware fallback for non-JSON responses.
  }
  return new Error(`AI readiness check failed with HTTP ${response.status}.`);
}

export function ResearchAIReadiness() {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestSequence = useRef(0);

  const refresh = useCallback(async () => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setLoading(true);
    try {
      const response = await apiFetch("/v1/ai/status", { cache: "no-store" });
      if (!response.ok) {
        throw await responseError(response);
      }
      const nextStatus = (await response.json()) as AIStatus;
      if (requestId !== requestSequence.current) {
        return;
      }
      setStatus(nextStatus);
      setError(null);
    } catch (caught) {
      if (requestId !== requestSequence.current) {
        return;
      }
      setStatus(null);
      setError(caught instanceof Error ? caught.message : "AI readiness could not be confirmed.");
    } finally {
      if (requestId === requestSequence.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };
    const handleFocus = () => void refresh();

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleFocus);
    return () => {
      requestSequence.current += 1;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleFocus);
    };
  }, [refresh]);

  if (loading && status === null && error === null) {
    return null;
  }

  if (status?.ready && status.state === "ready" && error === null) {
    return null;
  }

  const copy = status ? readinessCopy(status) : null;

  return (
    <section className={styles.notice} aria-labelledby="research-ai-readiness-heading">
      <div className={styles.copy}>
        <span className={styles.eyebrow}>Local AI readiness</span>
        <h2 id="research-ai-readiness-heading">
          {copy?.title ?? "AI readiness could not be confirmed"}
        </h2>
        <p>
          {copy?.message ??
            "Bukmatika could not confirm whether model-backed research is ready. Evidence-only research remains available."}
        </p>
        {error ? <small role="alert">{error}</small> : null}
        {status?.provider || status?.model ? (
          <div className={styles.facts} aria-label="Current local AI selection">
            {status.provider ? <span>Provider: {status.provider}</span> : null}
            {status.model ? <span>Model: {status.model}</span> : null}
            {status.routing ? <span>Routing: {status.routing}</span> : null}
          </div>
        ) : null}
      </div>
      <div className={styles.actions}>
        <a href="/personalization">Open AI setup</a>
        <button type="button" disabled={loading} onClick={() => void refresh()}>
          {loading ? "Checking…" : "Check again"}
        </button>
      </div>
    </section>
  );
}

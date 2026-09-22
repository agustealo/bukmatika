"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./local-ai-status.module.css";

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

type StatusPresentation = {
  title: string;
  summary: string;
  recovery: string | null;
  tone: "ready" | "attention" | "quiet";
};

function presentation(status: AIStatus): StatusPresentation {
  switch (status.state) {
    case "ready":
      return {
        title: "Ready",
        summary:
          "The local runtime is reachable and the configured model is installed. Grounded research can use local synthesis.",
        recovery: null,
        tone: "ready",
      };
    case "ai_disabled":
      return {
        title: "AI disabled",
        summary:
          "Model-backed features are off for this profile. Normal library, reader, and evidence features remain available.",
        recovery:
          "Enable AI assistance in the control center above, then check again. Bukmatika intentionally skips runtime probes while AI is disabled.",
        tone: "quiet",
      };
    case "unconfigured":
      return {
        title: "Not configured",
        summary:
          "This API process has no active local model provider, so Bukmatika will stay evidence-only.",
        recovery:
          "Configure BUKMATIKA_MODEL_PROVIDER=ollama and BUKMATIKA_OLLAMA_MODEL with an installed local model, restart the API process, then check again.",
        tone: "attention",
      };
    case "provider_unreachable":
      return {
        title: "Runtime offline",
        summary:
          "Bukmatika cannot reach the configured Ollama runtime on its loopback-only endpoint.",
        recovery:
          "Start or restart the local Ollama runtime at the configured loopback address, then check again. Remote model endpoints are not accepted by this provider.",
        tone: "attention",
      };
    case "provider_invalid":
      return {
        title: "Runtime response invalid",
        summary:
          "The configured local endpoint responded, but it did not satisfy the expected Ollama model-list contract.",
        recovery:
          "Confirm the configured URL points to the Ollama server root and that the local runtime is compatible, then check again.",
        tone: "attention",
      };
    case "model_missing":
      return {
        title: "Model missing",
        summary: status.model
          ? `Ollama is reachable, but ${status.model} is not installed in that runtime.`
          : "Ollama is reachable, but the configured model is not installed in that runtime.",
        recovery: status.model
          ? `Install ${status.model} in the local Ollama runtime, then check again.`
          : "Install the configured model in the local Ollama runtime, then check again.",
        tone: "attention",
      };
  }
}

async function statusError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return new Error(payload.detail);
    }
  } catch {
    // Use the status fallback below for non-JSON responses.
  }
  return new Error(`Could not inspect local AI readiness (HTTP ${response.status}).`);
}

function fact(value: string | null): string {
  return value?.trim() || "Not configured";
}

export function LocalAIStatus() {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/v1/ai/status");
      if (!response.ok) {
        throw await statusError(response);
      }
      setStatus((await response.json()) as AIStatus);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not inspect local AI readiness.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const state = status ? presentation(status) : null;

  return (
    <section className={styles.surface} aria-labelledby="local-ai-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>05</span>
          <div>
            <span className="eyebrow">Local AI runtime</span>
            <h2 id="local-ai-heading">Provider readiness</h2>
          </div>
        </div>
        <p>
          Bukmatika checks runtime and installed-model metadata only. The readiness probe never sends
          book text, research questions, annotations, or other private reading context.
        </p>
      </div>

      <div className={styles.panel}>
        <div className={styles.statusBlock}>
          <div className={styles.statusTopline}>
            <span
              className={styles.badge}
              data-tone={state?.tone ?? "quiet"}
              aria-live="polite"
            >
              {loading ? "Checking…" : state?.title ?? "Unavailable"}
            </span>
            <button className={styles.refresh} type="button" disabled={loading} onClick={() => void refresh()}>
              {loading ? "Checking" : "Check again"}
            </button>
          </div>

          {error ? (
            <div className={styles.error} role="alert">
              <strong>Readiness check failed</strong>
              <p>{error}</p>
            </div>
          ) : null}

          {state ? (
            <div className={styles.message}>
              <p>{state.summary}</p>
              {state.recovery ? (
                <div className={styles.recovery}>
                  <span>Recovery</span>
                  <p>{state.recovery}</p>
                </div>
              ) : (
                <div className={styles.recovery} data-ready="true">
                  <span>Boundary</span>
                  <p>
                    Local synthesis remains policy-gated and citation-validated. Readiness alone does
                    not authorize an action.
                  </p>
                </div>
              )}
            </div>
          ) : null}
        </div>

        <dl className={styles.facts}>
          <div>
            <dt>Provider</dt>
            <dd>{fact(status?.provider ?? null)}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{fact(status?.model ?? null)}</dd>
          </div>
          <div>
            <dt>Routing</dt>
            <dd>{fact(status?.routing ?? null)}</dd>
          </div>
          <div>
            <dt>Profile AI</dt>
            <dd>{status ? (status.ai_enabled ? "Enabled" : "Disabled") : "Unknown"}</dd>
          </div>
        </dl>
      </div>

      <p className={styles.footnote}>
        Provider configuration is process-owned in this web build. This surface inspects and explains
        the canonical runtime state; it does not write server environment configuration.
      </p>
    </section>
  );
}

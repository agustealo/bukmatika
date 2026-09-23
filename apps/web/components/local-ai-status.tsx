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

type ModelConfigurationMode = "installation_default" | "disabled" | "ollama";

type ModelConfiguration = {
  mode: ModelConfigurationMode;
  source: "installation" | "profile";
  selected_model: string | null;
  effective_provider: string | null;
  effective_model: string | null;
  installation_provider: string | null;
  installation_model: string | null;
  routing: "local";
};

type ModelInventory = {
  state:
    | "unconfigured"
    | "provider_unreachable"
    | "provider_invalid"
    | "model_missing"
    | "ready";
  models: string[];
  routing: "local";
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
          "The local runtime is reachable and this profile's selected model is installed. Grounded research can use local synthesis.",
        recovery: null,
        tone: "ready",
      };
    case "ai_disabled":
      return {
        title: "AI disabled",
        summary:
          "Model-backed features are off for this profile. Normal library, reader, and evidence features remain available.",
        recovery:
          "Enable AI assistance in the control center above to inspect installed models. Bukmatika intentionally performs zero runtime probes while AI is disabled.",
        tone: "quiet",
      };
    case "unconfigured":
      return {
        title: "No model selected",
        summary:
          "This profile currently has no effective local model, so Bukmatika will stay evidence-only.",
        recovery:
          "Choose an installed Ollama model below, or use the installation default if one is configured.",
        tone: "attention",
      };
    case "provider_unreachable":
      return {
        title: "Runtime offline",
        summary:
          "Bukmatika cannot reach Ollama on the installation-controlled loopback endpoint.",
        recovery:
          "Start or restart Ollama locally, then refresh. The consumer model picker cannot redirect private context to a remote endpoint.",
        tone: "attention",
      };
    case "provider_invalid":
      return {
        title: "Runtime response invalid",
        summary:
          "The configured loopback endpoint responded, but it did not satisfy the expected Ollama model-list contract.",
        recovery:
          "Check the installation-level Ollama endpoint and local runtime compatibility, then refresh.",
        tone: "attention",
      };
    case "model_missing":
      return {
        title: "Selected model missing",
        summary: status.model
          ? `Ollama is reachable, but ${status.model} is not installed in that runtime.`
          : "Ollama is reachable, but this profile's selected model is not installed.",
        recovery:
          "Choose one of the installed models below, or install the intended model in Ollama and refresh.",
        tone: "attention",
      };
  }
}

async function responseError(response: Response, fallback: string): Promise<Error> {
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
    // Use the status fallback below for non-JSON responses.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

function fact(value: string | null): string {
  return value?.trim() || "Not configured";
}

function sourceLabel(configuration: ModelConfiguration | null): string {
  if (!configuration) {
    return "Unknown";
  }
  if (configuration.mode === "installation_default") {
    return "Installation default";
  }
  if (configuration.mode === "disabled") {
    return "Disabled for this profile";
  }
  return "Profile selection";
}

export function LocalAIStatus() {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [configuration, setConfiguration] = useState<ModelConfiguration | null>(null);
  const [inventory, setInventory] = useState<ModelInventory | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResponse, configurationResponse] = await Promise.all([
        apiFetch("/v1/ai/status"),
        apiFetch("/v1/ai/configuration"),
      ]);
      if (!statusResponse.ok) {
        throw await responseError(statusResponse, "Could not inspect local AI readiness");
      }
      if (!configurationResponse.ok) {
        throw await responseError(configurationResponse, "Could not load local AI configuration");
      }

      const nextStatus = (await statusResponse.json()) as AIStatus;
      const nextConfiguration = (await configurationResponse.json()) as ModelConfiguration;
      setStatus(nextStatus);
      setConfiguration(nextConfiguration);

      let nextInventory: ModelInventory | null = null;
      if (nextStatus.ai_enabled) {
        const inventoryResponse = await apiFetch("/v1/ai/local/models");
        if (!inventoryResponse.ok) {
          throw await responseError(inventoryResponse, "Could not inspect installed local models");
        }
        nextInventory = (await inventoryResponse.json()) as ModelInventory;
      }
      setInventory(nextInventory);

      const preferred = nextConfiguration.selected_model ?? nextConfiguration.effective_model ?? "";
      if (nextInventory?.models.length) {
        setSelectedModel(
          nextInventory.models.includes(preferred) ? preferred : nextInventory.models[0] ?? "",
        );
      } else {
        setSelectedModel(preferred);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not inspect local AI readiness.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const saveConfiguration = useCallback(
    async (mode: ModelConfigurationMode, model: string | null = null) => {
      setSaving(true);
      setError(null);
      try {
        const response = await apiFetch("/v1/ai/configuration", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, model }),
        });
        if (!response.ok) {
          throw await responseError(response, "Could not save local AI configuration");
        }
        await refresh();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Could not save local AI configuration.");
      } finally {
        setSaving(false);
      }
    },
    [refresh],
  );

  const state = status ? presentation(status) : null;
  const installedModels = inventory?.state === "ready" ? inventory.models : [];
  const controlsDisabled = loading || saving;

  return (
    <section className={styles.surface} aria-labelledby="local-ai-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>05</span>
          <div>
            <span className="eyebrow">Local AI runtime</span>
            <h2 id="local-ai-heading">Model setup & readiness</h2>
          </div>
        </div>
        <p>
          Choose which installed local model this profile uses. Runtime discovery reads Ollama model
          metadata only and never sends book text, research questions, annotations, or reading context.
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
            <button
              className={styles.refresh}
              type="button"
              disabled={controlsDisabled}
              onClick={() => void refresh()}
            >
              {loading ? "Checking" : "Refresh local models"}
            </button>
          </div>

          {error ? (
            <div className={styles.error} role="alert">
              <strong>Local AI setup needs attention</strong>
              <p>{error}</p>
            </div>
          ) : null}

          {state ? (
            <div className={styles.message}>
              <p>{state.summary}</p>
              {state.recovery ? (
                <div className={styles.recovery}>
                  <span>Next step</span>
                  <p>{state.recovery}</p>
                </div>
              ) : (
                <div className={styles.recovery} data-ready="true">
                  <span>Boundary</span>
                  <p>
                    Local synthesis remains policy-gated and citation-validated. Model readiness does
                    not authorize an action or widen the selected research corpus.
                  </p>
                </div>
              )}
            </div>
          ) : null}
        </div>

        <dl className={styles.facts}>
          <div>
            <dt>Provider</dt>
            <dd>{fact(status?.provider ?? configuration?.effective_provider ?? null)}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{fact(status?.model ?? configuration?.effective_model ?? null)}</dd>
          </div>
          <div>
            <dt>Routing</dt>
            <dd>{fact(status?.routing ?? configuration?.routing ?? null)}</dd>
          </div>
          <div>
            <dt>Configuration</dt>
            <dd>{sourceLabel(configuration)}</dd>
          </div>
        </dl>
      </div>

      <div className={styles.configurationPanel}>
        <div className={styles.configurationCopy}>
          <span className={styles.label}>Profile model selection</span>
          <h3>Use an installed Ollama model without restarting Bukmatika.</h3>
          <p>
            Model choice belongs to this profile. The Ollama address, routing policy, and timeouts stay
            installation-controlled and loopback-only.
          </p>
        </div>

        <div className={styles.configurationControls}>
          {status?.ai_enabled ? (
            <>
              {inventory?.state === "ready" && installedModels.length > 0 ? (
                <div className={styles.modelPicker}>
                  <label htmlFor="local-ai-model">Installed model</label>
                  <div>
                    <select
                      id="local-ai-model"
                      value={selectedModel}
                      disabled={controlsDisabled}
                      onChange={(event) => setSelectedModel(event.target.value)}
                    >
                      {installedModels.map((model) => (
                        <option key={model} value={model}>
                          {model}
                        </option>
                      ))}
                    </select>
                    <button
                      className={styles.primaryAction}
                      type="button"
                      disabled={controlsDisabled || !selectedModel}
                      onClick={() => void saveConfiguration("ollama", selectedModel)}
                    >
                      {saving ? "Saving…" : "Use selected model"}
                    </button>
                  </div>
                </div>
              ) : null}

              {inventory?.state === "ready" && installedModels.length === 0 ? (
                <p className={styles.inventoryNote}>
                  Ollama is reachable, but it reports no installed models. Install a model in Ollama,
                  then refresh this card.
                </p>
              ) : null}

              {inventory && inventory.state !== "ready" ? (
                <p className={styles.inventoryNote}>
                  Installed models cannot be listed until the local Ollama runtime is reachable and
                  returns valid metadata.
                </p>
              ) : null}
            </>
          ) : (
            <p className={styles.inventoryNote}>
              AI is disabled for this profile, so Bukmatika does not probe the local runtime. Enable AI
              above before scanning installed models.
            </p>
          )}

          <div className={styles.secondaryActions}>
            <button
              type="button"
              disabled={controlsDisabled || configuration?.mode === "installation_default"}
              onClick={() => void saveConfiguration("installation_default")}
            >
              Use installation default
            </button>
            <button
              type="button"
              disabled={controlsDisabled || configuration?.mode === "disabled"}
              onClick={() => void saveConfiguration("disabled")}
            >
              Disable model for this profile
            </button>
          </div>

          <p className={styles.installationDefault}>
            Installation default: {fact(configuration?.installation_provider ?? null)} ·{" "}
            {fact(configuration?.installation_model ?? null)}
          </p>
        </div>
      </div>

      <p className={styles.footnote}>
        This surface can select only local Ollama models reported by the installation-controlled
        loopback runtime. It cannot add remote providers, change the runtime URL, or transmit private
        research context during model discovery.
      </p>
    </section>
  );
}

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
          status.routing === "local"
            ? "The local runtime is reachable and this profile's selected model is installed. Grounded research can use local synthesis."
            : "The selected AI provider is ready for this profile. Grounded research remains subject to Bukmatika's privacy and citation policies.",
        recovery: null,
        tone: "ready",
      };
    case "ai_disabled":
      return {
        title: "AI disabled",
        summary:
          "Model-backed features are off for this profile. Normal library, reader, and evidence features remain available.",
        recovery:
          "Enable AI assistance in the control center above to inspect configured model readiness. Bukmatika intentionally performs zero provider probes while AI is disabled.",
        tone: "quiet",
      };
    case "unconfigured":
      return {
        title: "No model selected",
        summary:
          "This profile currently has no effective model, so Bukmatika will stay evidence-only.",
        recovery:
          "Choose a configured model, or use the installation default if one is available.",
        tone: "attention",
      };
    case "provider_unreachable":
      return {
        title: "Runtime offline",
        summary:
          status.routing === "local"
            ? "Bukmatika cannot reach the selected local model runtime."
            : "Bukmatika cannot reach the selected AI provider.",
        recovery:
          status.routing === "local"
            ? "Start or restart the local runtime, then refresh. Bukmatika will not silently redirect private context to a cloud provider."
            : "Check the configured provider connection and try again. Bukmatika will not silently fall back to another provider.",
        tone: "attention",
      };
    case "provider_invalid":
      return {
        title: "Runtime response invalid",
        summary:
          "The selected provider responded, but the response did not satisfy Bukmatika's expected runtime contract.",
        recovery:
          "Check provider compatibility and configuration, then refresh. Bukmatika will fail closed rather than reinterpret an invalid provider response.",
        tone: "attention",
      };
    case "model_missing":
      return {
        title: "Selected model missing",
        summary: status.model
          ? `${status.model} is not available from the selected provider.`
          : "The selected provider is reachable, but this profile's model is unavailable.",
        recovery:
          status.routing === "local"
            ? "Choose one of the installed local models below, or install the intended model and refresh."
            : "Choose another configured model for this provider or restore access to the selected model.",
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
        throw await responseError(statusResponse, "Could not inspect AI readiness");
      }
      if (!configurationResponse.ok) {
        throw await responseError(configurationResponse, "Could not load AI configuration");
      }

      const nextStatus = (await statusResponse.json()) as AIStatus;
      const nextConfiguration = (await configurationResponse.json()) as ModelConfiguration;
      setStatus(nextStatus);
      setConfiguration(nextConfiguration);

      let nextInventory: ModelInventory | null = null;
      if (nextStatus.ai_enabled && nextStatus.routing === "local") {
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
      setError(caught instanceof Error ? caught.message : "Could not inspect AI readiness.");
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
  const localControlsAvailable = status?.routing === "local" || status?.routing == null;

  return (
    <section className={styles.surface} aria-labelledby="local-ai-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>05</span>
          <div>
            <span className="eyebrow">AI runtime</span>
            <h2 id="local-ai-heading">Model setup & readiness</h2>
          </div>
        </div>
        <p>
          Inspect the provider and model selected for this profile. Local model discovery never sends
          book text, research questions, annotations, or reading context.
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
              {loading ? "Checking" : "Refresh model status"}
            </button>
          </div>

          {error ? (
            <div className={styles.error} role="alert">
              <strong>AI setup needs attention</strong>
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
                    Model use remains policy-gated and citation-validated. Readiness does not authorize
                    an action, widen the selected research corpus, or approve cross-provider fallback.
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

      {localControlsAvailable ? (
        <div className={styles.configurationPanel}>
          <div className={styles.configurationCopy}>
            <span className={styles.label}>Local profile model selection</span>
            <h3>Use an installed Ollama model without restarting Bukmatika.</h3>
            <p>
              This transitional local-provider control only manages Ollama. Provider-neutral cloud
              connection management is handled by the upcoming provider settings surface.
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
                AI is disabled for this profile, so Bukmatika does not probe the local runtime. Enable
                AI above before scanning installed models.
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
      ) : (
        <div className={styles.configurationPanel}>
          <div className={styles.configurationCopy}>
            <span className={styles.label}>Cloud provider selection</span>
            <h3>This profile is routed through a cloud provider.</h3>
            <p>
              Bukmatika will not probe Ollama or silently replace this provider with a local model. Full
              provider and role management will be surfaced through the provider-neutral settings flow.
            </p>
          </div>
        </div>
      )}

      <p className={styles.footnote}>
        Local model discovery is limited to the installation-controlled Ollama loopback runtime. Cloud
        routing never causes a local inventory probe, and provider fallback is not automatic.
      </p>
    </section>
  );
}

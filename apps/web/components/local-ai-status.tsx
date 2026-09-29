"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./local-ai-status.module.css";

type RoutingType = "local" | "cloud";
type ConnectionStatus = "enabled" | "disabled";
type ModelRole = "primary" | "research" | "fast" | "reasoning" | "embeddings" | "fallback";
type ModelSelectionMode = "installation_default" | "profile";
type CloudEgressPolicy = "local_only" | "public_only" | "private_context";

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

type RoutingPolicy = {
  ai_enabled: boolean;
  model_selection_mode: ModelSelectionMode;
  cloud_egress_policy: CloudEgressPolicy;
};

type ProviderDescriptor = {
  provider_id: string;
  display_name: string;
  routing_type: RoutingType;
};

type ProviderConnection = {
  connection_id: string;
  provider_id: string;
  display_name: string | null;
  routing_type: RoutingType;
  status: ConnectionStatus;
  credential_configured: boolean;
  created_at: string;
  updated_at: string;
};

type ModelAssignment = {
  assignment_id: string;
  connection_id: string;
  role: ModelRole;
  model_id: string;
  priority: number;
  enabled: boolean;
  capabilities: string[];
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

const ROLES: ModelRole[] = [
  "primary",
  "research",
  "fast",
  "reasoning",
  "embeddings",
  "fallback",
];

function presentation(status: AIStatus): StatusPresentation {
  switch (status.state) {
    case "ready":
      return {
        title: "Ready",
        summary: `Bukmatika is ready to use ${status.provider ?? "the selected provider"}${
          status.model ? ` · ${status.model}` : ""
        } for the active profile route.`,
        recovery: null,
        tone: "ready",
      };
    case "ai_disabled":
      return {
        title: "AI disabled",
        summary: "Model-backed features are off. Library, reader, search, notes, and evidence remain available.",
        recovery: "Enable AI below when you want Bukmatika to inspect provider readiness again.",
        tone: "quiet",
      };
    case "unconfigured":
      return {
        title: "No usable route",
        summary: "No provider/model route is currently usable under this profile and its privacy policy.",
        recovery: "Choose an installation default or assign a model to a profile role below.",
        tone: "attention",
      };
    case "provider_unreachable":
      return {
        title: "Provider unreachable",
        summary: "Bukmatika cannot reach the selected provider.",
        recovery: "Check that provider connection. Bukmatika will not silently fall back to another provider.",
        tone: "attention",
      };
    case "provider_invalid":
      return {
        title: "Provider response invalid",
        summary: "The provider answered, but its response did not satisfy Bukmatika's runtime contract.",
        recovery: "Review the connection and model. Invalid responses fail closed.",
        tone: "attention",
      };
    case "model_missing":
      return {
        title: "Model unavailable",
        summary: status.model
          ? `${status.model} is unavailable from the selected provider.`
          : "The selected provider is reachable, but its assigned model is unavailable.",
        recovery: "Assign an available model without changing another provider connection.",
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
    // Preserve the status fallback for non-JSON responses.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

function providerLabel(provider: ProviderDescriptor | undefined, fallback: string): string {
  return provider?.display_name ?? fallback;
}

function roleLabel(role: ModelRole): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}

function egressCopy(policy: CloudEgressPolicy): string {
  if (policy === "local_only") {
    return "Local only. Cloud providers cannot receive model requests or private context.";
  }
  if (policy === "public_only") {
    return "Cloud providers may receive public/evidence-only requests. Private user context remains local.";
  }
  return "Cloud providers may receive private library or research context when the selected route requires it.";
}

export function LocalAIStatus() {
  const [status, setStatus] = useState<AIStatus | null>(null);
  const [policy, setPolicy] = useState<RoutingPolicy | null>(null);
  const [providers, setProviders] = useState<ProviderDescriptor[]>([]);
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [assignments, setAssignments] = useState<ModelAssignment[]>([]);
  const [inventory, setInventory] = useState<ModelInventory | null>(null);
  const [newProviderId, setNewProviderId] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [assignmentRole, setAssignmentRole] = useState<ModelRole>("primary");
  const [assignmentConnectionId, setAssignmentConnectionId] = useState("");
  const [assignmentModel, setAssignmentModel] = useState("");
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const providerById = useMemo(
    () => new Map(providers.map((provider) => [provider.provider_id, provider])),
    [providers],
  );
  const connectionById = useMemo(
    () => new Map(connections.map((connection) => [connection.connection_id, connection])),
    [connections],
  );
  const enabledConnections = connections.filter((connection) => connection.status === "enabled");
  const assignmentConnection = connectionById.get(assignmentConnectionId);
  const state = status ? presentation(status) : null;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusResponse, policyResponse, providersResponse, connectionsResponse, assignmentsResponse] =
        await Promise.all([
          apiFetch("/v1/ai/status"),
          apiFetch("/v1/ai/routing-policy"),
          apiFetch("/v1/ai/providers"),
          apiFetch("/v1/ai/provider-connections"),
          apiFetch("/v1/ai/model-assignments"),
        ]);
      const responses = [
        [statusResponse, "Could not inspect AI readiness"],
        [policyResponse, "Could not load AI routing policy"],
        [providersResponse, "Could not load provider catalog"],
        [connectionsResponse, "Could not load provider connections"],
        [assignmentsResponse, "Could not load model assignments"],
      ] as const;
      for (const [response, fallback] of responses) {
        if (!response.ok) {
          throw await responseError(response, fallback);
        }
      }

      const nextStatus = (await statusResponse.json()) as AIStatus;
      const nextPolicy = (await policyResponse.json()) as RoutingPolicy;
      const nextProviders = (await providersResponse.json()) as { providers: ProviderDescriptor[] };
      const nextConnections = (await connectionsResponse.json()) as { connections: ProviderConnection[] };
      const nextAssignments = (await assignmentsResponse.json()) as { assignments: ModelAssignment[] };
      setStatus(nextStatus);
      setPolicy(nextPolicy);
      setProviders(nextProviders.providers);
      setConnections(nextConnections.connections);
      setAssignments(nextAssignments.assignments);
      setNewProviderId((current) => current || nextProviders.providers[0]?.provider_id || "");
      setAssignmentConnectionId((current) => {
        if (current && nextConnections.connections.some((item) => item.connection_id === current && item.status === "enabled")) {
          return current;
        }
        return nextConnections.connections.find((item) => item.status === "enabled")?.connection_id ?? "";
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load AI provider settings.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const mutate = useCallback(
    async (operation: () => Promise<Response>, fallback: string) => {
      setSaving(true);
      setError(null);
      try {
        const response = await operation();
        if (!response.ok) {
          throw await responseError(response, fallback);
        }
        await refresh();
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : fallback);
      } finally {
        setSaving(false);
      }
    },
    [refresh],
  );

  const updatePolicy = useCallback(
    async (next: Partial<RoutingPolicy>) => {
      if (!policy) return;
      await mutate(
        () =>
          apiFetch("/v1/ai/routing-policy", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...policy, ...next }),
          }),
        "Could not update AI routing policy",
      );
    },
    [mutate, policy],
  );

  const createConnection = useCallback(async () => {
    if (!newProviderId) return;
    await mutate(
      () =>
        apiFetch("/v1/ai/provider-connections", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider_id: newProviderId,
            display_name: newDisplayName.trim() || null,
          }),
        }),
      "Could not create provider connection",
    );
    setNewDisplayName("");
  }, [mutate, newDisplayName, newProviderId]);

  const setConnectionEnabled = useCallback(
    async (connection: ProviderConnection, enabled: boolean) => {
      await mutate(
        () =>
          apiFetch(`/v1/ai/provider-connections/${connection.connection_id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ display_name: connection.display_name, enabled }),
          }),
        "Could not update provider connection",
      );
    },
    [mutate],
  );

  const disconnect = useCallback(
    async (connectionId: string) => {
      await mutate(
        () => apiFetch(`/v1/ai/provider-connections/${connectionId}`, { method: "DELETE" }),
        "Could not disconnect provider",
      );
    },
    [mutate],
  );

  const saveCredential = useCallback(
    async (connectionId: string) => {
      const secret = credentialDrafts[connectionId]?.trim();
      if (!secret) return;
      await mutate(
        () =>
          apiFetch(`/v1/ai/provider-connections/${connectionId}/credential`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ secret }),
          }),
        "Could not save provider credential",
      );
      setCredentialDrafts((current) => ({ ...current, [connectionId]: "" }));
    },
    [credentialDrafts, mutate],
  );

  const deleteCredential = useCallback(
    async (connectionId: string) => {
      await mutate(
        () =>
          apiFetch(`/v1/ai/provider-connections/${connectionId}/credential`, {
            method: "DELETE",
          }),
        "Could not remove provider credential",
      );
    },
    [mutate],
  );

  const assignModel = useCallback(async () => {
    if (!assignmentConnectionId || !assignmentModel.trim()) return;
    await mutate(
      () =>
        apiFetch(`/v1/ai/model-assignments/${assignmentRole}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            connection_id: assignmentConnectionId,
            model_id: assignmentModel.trim(),
            priority: 0,
          }),
        }),
      "Could not assign model role",
    );
  }, [assignmentConnectionId, assignmentModel, assignmentRole, mutate]);

  const loadOllamaModels = useCallback(async () => {
    if (!policy?.ai_enabled) return;
    setSaving(true);
    setError(null);
    try {
      const response = await apiFetch("/v1/ai/local/models");
      if (!response.ok) {
        throw await responseError(response, "Could not inspect installed Ollama models");
      }
      const nextInventory = (await response.json()) as ModelInventory;
      setInventory(nextInventory);
      if (!assignmentModel && nextInventory.models.length > 0) {
        setAssignmentModel(nextInventory.models[0] ?? "");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not inspect installed Ollama models.");
    } finally {
      setSaving(false);
    }
  }, [assignmentModel, policy?.ai_enabled]);

  const controlsDisabled = loading || saving;

  return (
    <section className={styles.surface} aria-labelledby="ai-control-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>05</span>
          <div>
            <span className="eyebrow">AI control center</span>
            <h2 id="ai-control-heading">Providers, models & privacy</h2>
          </div>
        </div>
        <p>
          Choose which providers Bukmatika may use, which model owns each role, and what data may leave
          your device. Provider fallback is never automatic.
        </p>
      </div>

      <div className={styles.panel}>
        <div className={styles.statusBlock}>
          <div className={styles.statusTopline}>
            <span className={styles.badge} data-tone={state?.tone ?? "quiet"} aria-live="polite">
              {loading ? "Checking…" : state?.title ?? "Unavailable"}
            </span>
            <button className={styles.refresh} type="button" disabled={controlsDisabled} onClick={() => void refresh()}>
              {loading ? "Checking" : "Refresh status"}
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
              <div className={styles.recovery} data-ready={state.recovery ? undefined : "true"}>
                <span>{state.recovery ? "Next step" : "Boundary"}</span>
                <p>
                  {state.recovery ??
                    "Readiness does not widen research evidence, authorize an action, or permit an unapproved provider fallback."}
                </p>
              </div>
            </div>
          ) : null}
        </div>

        <dl className={styles.facts}>
          <div><dt>Provider</dt><dd>{status?.provider ?? "Not configured"}</dd></div>
          <div><dt>Model</dt><dd>{status?.model ?? "Not configured"}</dd></div>
          <div><dt>Routing</dt><dd>{status?.routing ?? "Not configured"}</dd></div>
          <div><dt>Selection</dt><dd>{policy?.model_selection_mode === "profile" ? "Profile roles" : "Installation default"}</dd></div>
        </dl>
      </div>

      <div className={styles.configurationPanel}>
        <div className={styles.configurationCopy}>
          <span className={styles.label}>Routing & privacy</span>
          <h3>Make external AI use an explicit choice.</h3>
          <p>{policy ? egressCopy(policy.cloud_egress_policy) : "Loading routing policy…"}</p>
        </div>
        <div className={styles.configurationControls}>
          <label className={styles.controlLabel} htmlFor="ai-enabled">AI assistance</label>
          <select
            id="ai-enabled"
            value={policy?.ai_enabled ? "enabled" : "disabled"}
            disabled={controlsDisabled || !policy}
            onChange={(event) => void updatePolicy({ ai_enabled: event.target.value === "enabled" })}
          >
            <option value="enabled">Enabled</option>
            <option value="disabled">Disabled, zero provider probes</option>
          </select>

          <label className={styles.controlLabel} htmlFor="model-selection-mode">Model selection</label>
          <select
            id="model-selection-mode"
            value={policy?.model_selection_mode ?? "installation_default"}
            disabled={controlsDisabled || !policy}
            onChange={(event) => void updatePolicy({ model_selection_mode: event.target.value as ModelSelectionMode })}
          >
            <option value="installation_default">Use installation default</option>
            <option value="profile">Use profile role assignments</option>
          </select>

          <label className={styles.controlLabel} htmlFor="cloud-egress-policy">Cloud data access</label>
          <select
            id="cloud-egress-policy"
            value={policy?.cloud_egress_policy ?? "local_only"}
            disabled={controlsDisabled || !policy}
            onChange={(event) => void updatePolicy({ cloud_egress_policy: event.target.value as CloudEgressPolicy })}
          >
            <option value="local_only">Local only</option>
            <option value="public_only">Cloud: public/evidence-only data</option>
            <option value="private_context">Cloud: allow selected private context</option>
          </select>
          {policy?.cloud_egress_policy === "private_context" ? (
            <p className={styles.warning}>
              Cloud providers are external services. Selected private library or research context may be sent to the active cloud provider under this policy.
            </p>
          ) : null}
        </div>
      </div>

      <div className={styles.sectionHeader}>
        <div>
          <span className={styles.label}>Provider connections</span>
          <h3>Configured runtimes</h3>
        </div>
        <p>Credentials are write-only. Bukmatika reports only whether a credential is configured.</p>
      </div>

      <div className={styles.addConnection}>
        <select value={newProviderId} disabled={controlsDisabled || providers.length === 0} onChange={(event) => setNewProviderId(event.target.value)} aria-label="Provider">
          {providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.display_name} · {provider.routing_type}</option>)}
        </select>
        <input value={newDisplayName} disabled={controlsDisabled} onChange={(event) => setNewDisplayName(event.target.value)} placeholder="Optional connection name" aria-label="Connection name" />
        <button className={styles.primaryAction} type="button" disabled={controlsDisabled || !newProviderId} onClick={() => void createConnection()}>
          Add connection
        </button>
      </div>

      <div className={styles.connectionGrid}>
        {connections.length === 0 ? <p className={styles.emptyState}>No provider connections configured yet.</p> : null}
        {connections.map((connection) => {
          const descriptor = providerById.get(connection.provider_id);
          return (
            <article className={styles.connectionCard} key={connection.connection_id}>
              <div className={styles.connectionTopline}>
                <div>
                  <span className={styles.routePill}>{connection.routing_type}</span>
                  <h4>{connection.display_name ?? providerLabel(descriptor, connection.provider_id)}</h4>
                  <p>{providerLabel(descriptor, connection.provider_id)} · {connection.status}</p>
                </div>
                <span className={styles.credentialState}>
                  {connection.routing_type === "local"
                    ? "No credential required"
                    : connection.credential_configured
                      ? "Credential configured"
                      : "Credential missing"}
                </span>
              </div>

              {connection.routing_type === "cloud" ? (
                <div className={styles.credentialControls}>
                  <input
                    type="password"
                    autoComplete="off"
                    value={credentialDrafts[connection.connection_id] ?? ""}
                    disabled={controlsDisabled}
                    onChange={(event) => setCredentialDrafts((current) => ({ ...current, [connection.connection_id]: event.target.value }))}
                    placeholder="Paste provider API key"
                    aria-label={`Credential for ${providerLabel(descriptor, connection.provider_id)}`}
                  />
                  <button type="button" disabled={controlsDisabled || !(credentialDrafts[connection.connection_id]?.trim())} onClick={() => void saveCredential(connection.connection_id)}>
                    Replace credential
                  </button>
                  {connection.credential_configured ? (
                    <button type="button" disabled={controlsDisabled} onClick={() => void deleteCredential(connection.connection_id)}>Remove credential</button>
                  ) : null}
                </div>
              ) : null}

              <div className={styles.secondaryActions}>
                <button type="button" disabled={controlsDisabled} onClick={() => void setConnectionEnabled(connection, connection.status !== "enabled")}>
                  {connection.status === "enabled" ? "Disable" : "Enable"}
                </button>
                <button type="button" disabled={controlsDisabled} onClick={() => void disconnect(connection.connection_id)}>Disconnect</button>
              </div>
            </article>
          );
        })}
      </div>

      <div className={styles.configurationPanel}>
        <div className={styles.configurationCopy}>
          <span className={styles.label}>Model roles</span>
          <h3>Assign jobs, not vendors.</h3>
          <p>Primary, research, reasoning, fast, embeddings, and fallback roles point to explicit provider connections. No role silently changes provider on failure.</p>
        </div>
        <div className={styles.configurationControls}>
          <label className={styles.controlLabel} htmlFor="assignment-role">Role</label>
          <select id="assignment-role" value={assignmentRole} disabled={controlsDisabled} onChange={(event) => setAssignmentRole(event.target.value as ModelRole)}>
            {ROLES.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
          </select>

          <label className={styles.controlLabel} htmlFor="assignment-connection">Provider connection</label>
          <select id="assignment-connection" value={assignmentConnectionId} disabled={controlsDisabled || enabledConnections.length === 0} onChange={(event) => { setAssignmentConnectionId(event.target.value); setAssignmentModel(""); setInventory(null); }}>
            <option value="">Choose connection</option>
            {enabledConnections.map((connection) => <option key={connection.connection_id} value={connection.connection_id}>{connection.display_name ?? providerLabel(providerById.get(connection.provider_id), connection.provider_id)} · {connection.routing_type}</option>)}
          </select>

          {assignmentConnection?.provider_id === "ollama" ? (
            <button className={styles.refresh} type="button" disabled={controlsDisabled || !policy?.ai_enabled} onClick={() => void loadOllamaModels()}>
              Load installed Ollama models
            </button>
          ) : null}

          <label className={styles.controlLabel} htmlFor="assignment-model">Model</label>
          <input
            id="assignment-model"
            list={assignmentConnection?.provider_id === "ollama" ? "ollama-models" : undefined}
            value={assignmentModel}
            disabled={controlsDisabled || !assignmentConnectionId}
            onChange={(event) => setAssignmentModel(event.target.value)}
            placeholder="Provider model ID"
          />
          <datalist id="ollama-models">
            {inventory?.models.map((model) => <option key={model} value={model} />)}
          </datalist>
          {inventory ? <p className={styles.inventoryNote}>Ollama inventory: {inventory.state} · {inventory.models.length} model(s)</p> : null}
          <button className={styles.primaryAction} type="button" disabled={controlsDisabled || !assignmentConnectionId || !assignmentModel.trim()} onClick={() => void assignModel()}>
            Assign role
          </button>
        </div>
      </div>

      <div className={styles.assignmentGrid}>
        {assignments.length === 0 ? <p className={styles.emptyState}>No active profile role assignments.</p> : null}
        {assignments.map((assignment) => {
          const connection = connectionById.get(assignment.connection_id);
          return (
            <article className={styles.assignmentCard} key={assignment.assignment_id}>
              <span className={styles.label}>{roleLabel(assignment.role)}</span>
              <strong>{assignment.model_id}</strong>
              <p>{connection ? `${connection.display_name ?? providerLabel(providerById.get(connection.provider_id), connection.provider_id)} · ${connection.routing_type}` : "Connection unavailable"}</p>
              <small>{assignment.capabilities.join(" · ")}</small>
            </article>
          );
        })}
      </div>

      <p className={styles.footnote}>
        Local inventory is queried only when you explicitly request Ollama models. Refreshing this control center never probes Ollama on behalf of a cloud-selected route. Credentials are never returned to this page.
      </p>
    </section>
  );
}

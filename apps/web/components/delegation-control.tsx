"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./delegation-control.module.css";

type DelegationStatus =
  | "proposed"
  | "approved"
  | "rejected"
  | "running"
  | "stop_requested"
  | "stopped"
  | "completed"
  | "failed"
  | "cancelled";

type DelegationControlItem = {
  delegation_id: string;
  plan_id: string;
  status: DelegationStatus;
  step_ids: string[];
  current_step_index: number;
  remaining_steps: number;
  attempts_used: number;
  max_total_attempts: number;
  remaining_attempts: number;
  max_runtime_seconds: number;
  remaining_runtime_seconds: number;
  started_at: string | null;
  stop_requested_at: string | null;
};

type DelegationControlSnapshot = {
  ai_enabled: boolean;
  autonomy_level: number;
  level2_enabled: boolean;
  consent_scope: "read_only" | null;
  consent_policy_version: string | null;
  consented_at: string | null;
  revoked_at: string | null;
  active_delegations: DelegationControlItem[];
};

type BusyAction =
  | "grant"
  | "revoke"
  | `approve:${string}`
  | `reject:${string}`
  | `start:${string}`
  | `stop:${string}`
  | null;

const dateFormatter = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatDate(value: string | null): string {
  if (!value) {
    return "Not recorded";
  }
  return dateFormatter.format(new Date(value));
}

function formatDuration(seconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  if (minutes === 0) {
    return `${remainder}s`;
  }
  if (remainder === 0) {
    return `${minutes}m`;
  }
  return `${minutes}m ${remainder}s`;
}

function readableStep(stepId: string): string {
  return stepId.replaceAll("_", " ").replaceAll("-", " ");
}

function statusLabel(status: DelegationStatus): string {
  switch (status) {
    case "proposed":
      return "Awaiting approval";
    case "approved":
      return "Approved · ready to start";
    case "running":
      return "Running";
    case "stop_requested":
      return "Stop requested";
    case "rejected":
      return "Rejected";
    case "stopped":
      return "Stopped";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Cancelled";
  }
}

function statusTone(status: DelegationStatus): "ready" | "attention" | "quiet" {
  if (status === "running" || status === "approved") {
    return "ready";
  }
  if (status === "proposed" || status === "stop_requested") {
    return "attention";
  }
  return "quiet";
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
      const message = payload.detail.code.replaceAll("_", " ").toLowerCase();
      return new Error(message.charAt(0).toUpperCase() + message.slice(1));
    }
  } catch {
    // Keep the status-aware fallback for non-JSON responses.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

export function DelegationControl() {
  const [snapshot, setSnapshot] = useState<DelegationControlSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [consentAcknowledged, setConsentAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const response = await apiFetch("/v1/personalization/delegation-control");
    if (!response.ok) {
      throw await responseError(response, "Could not load delegation controls");
    }
    setSnapshot((await response.json()) as DelegationControlSnapshot);
  }, []);

  useEffect(() => {
    let active = true;

    void (async () => {
      try {
        await refresh();
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : "Could not load delegation controls.");
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    })();

    return () => {
      active = false;
    };
  }, [refresh]);

  const runningCount = useMemo(
    () =>
      snapshot?.active_delegations.filter(
        (delegation) => delegation.status === "running" || delegation.status === "stop_requested",
      ).length ?? 0,
    [snapshot],
  );

  const proposedCount = useMemo(
    () => snapshot?.active_delegations.filter((delegation) => delegation.status === "proposed").length ?? 0,
    [snapshot],
  );

  async function mutate(action: NonNullable<BusyAction>, request: () => Promise<Response>) {
    setBusyAction(action);
    setError(null);
    try {
      const response = await request();
      if (!response.ok) {
        throw await responseError(response, "The delegation change was not saved");
      }
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The delegation change failed.");
    } finally {
      setBusyAction(null);
    }
  }

  async function decideConsent(action: "grant" | "revoke") {
    await mutate(action, () =>
      apiFetch("/v1/personalization/delegation-control/consent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      }),
    );
    if (action === "revoke") {
      setConsentAcknowledged(false);
    }
  }

  async function decideDelegation(delegationId: string, decision: "approved" | "rejected") {
    const action = `${decision === "approved" ? "approve" : "reject"}:${delegationId}` as const;
    await mutate(action, () =>
      apiFetch(`/v1/ai/delegations/${delegationId}/approval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      }),
    );
  }

  async function startDelegation(delegationId: string) {
    await mutate(`start:${delegationId}`, () =>
      apiFetch(`/v1/personalization/delegations/${delegationId}/start`, {
        method: "POST",
      }),
    );
  }

  async function stopDelegation(delegationId: string) {
    await mutate(`stop:${delegationId}`, () =>
      apiFetch(`/v1/ai/delegations/${delegationId}/stop`, {
        method: "POST",
      }),
    );
  }

  const controlsDisabled = loading || busyAction !== null;
  const canGrant = Boolean(snapshot?.ai_enabled) && !snapshot?.level2_enabled;

  return (
    <section className={styles.surface} aria-labelledby="delegation-control-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>06</span>
          <div>
            <span className="eyebrow">Bounded delegation</span>
            <h2 id="delegation-control-heading">Level 2 autonomy controls</h2>
          </div>
        </div>
        <p>
          Level 2 lets Bukmatika continue an explicitly approved, read-only plan within fixed step,
          attempt, and runtime budgets. It does not schedule itself, write to your library, or grant
          standing permissions to future plans.
        </p>
      </div>

      {error ? (
        <div className="error-card" role="alert">
          {error}
        </div>
      ) : null}

      <div className={styles.controlGrid}>
        <article className={styles.consentCard}>
          <div className={styles.cardTopline}>
            <div>
              <span className={styles.label}>Delegation consent</span>
              <h3>{snapshot?.level2_enabled ? "Level 2 is enabled" : "Level 2 is off"}</h3>
            </div>
            <span
              className={styles.badge}
              data-tone={snapshot?.level2_enabled ? "ready" : "quiet"}
              aria-live="polite"
            >
              {loading ? "Loading" : snapshot?.level2_enabled ? "Read-only enabled" : "Disabled"}
            </span>
          </div>

          <p className={styles.cardCopy}>
            Consent applies only to the current read-only delegation policy. Every delegation still
            requires its own exact approval before it can start.
          </p>

          <dl className={styles.facts}>
            <div>
              <dt>AI assistance</dt>
              <dd>{snapshot?.ai_enabled ? "Enabled" : "Disabled"}</dd>
            </div>
            <div>
              <dt>Autonomy level</dt>
              <dd>{snapshot ? `Level ${snapshot.autonomy_level}` : "Loading"}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{snapshot?.consent_scope === "read_only" ? "Read-only" : "None"}</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>{snapshot?.consent_policy_version ?? "None"}</dd>
            </div>
            <div>
              <dt>Granted</dt>
              <dd>{formatDate(snapshot?.consented_at ?? null)}</dd>
            </div>
            <div>
              <dt>Last revoked</dt>
              <dd>{formatDate(snapshot?.revoked_at ?? null)}</dd>
            </div>
          </dl>

          {snapshot?.level2_enabled ? (
            <div className={styles.revokeBlock}>
              <p>
                Revoking consent immediately prevents new starts. Approved or proposed delegations are
                cancelled, while running work receives a durable stop request.
              </p>
              <button
                type="button"
                className={styles.dangerAction}
                disabled={controlsDisabled}
                onClick={() => void decideConsent("revoke")}
              >
                {busyAction === "revoke" ? "Revoking…" : "Revoke Level 2"}
              </button>
            </div>
          ) : (
            <div className={styles.grantBlock}>
              <label className={styles.acknowledgement}>
                <input
                  type="checkbox"
                  checked={consentAcknowledged}
                  disabled={controlsDisabled || !snapshot?.ai_enabled}
                  onChange={(event) => setConsentAcknowledged(event.target.checked)}
                />
                <span>
                  I understand this enables only approved read-only delegations with fixed budgets and
                  can be revoked at any time.
                </span>
              </label>
              {!snapshot?.ai_enabled && !loading ? (
                <p className={styles.notice}>Enable AI assistance above before granting Level 2.</p>
              ) : null}
              <button
                type="button"
                className={styles.primaryAction}
                disabled={controlsDisabled || !canGrant || !consentAcknowledged}
                onClick={() => void decideConsent("grant")}
              >
                {busyAction === "grant" ? "Enabling…" : "Enable read-only Level 2"}
              </button>
            </div>
          )}
        </article>

        <article className={styles.summaryCard}>
          <span className={styles.label}>Current queue</span>
          <div className={styles.summaryNumbers}>
            <div>
              <strong>{proposedCount}</strong>
              <span>Awaiting approval</span>
            </div>
            <div>
              <strong>{runningCount}</strong>
              <span>Running / stopping</span>
            </div>
            <div>
              <strong>{snapshot?.active_delegations.length ?? 0}</strong>
              <span>Nonterminal total</span>
            </div>
          </div>
          <button
            type="button"
            className={styles.refreshAction}
            disabled={controlsDisabled}
            onClick={() => {
              setLoading(true);
              setError(null);
              void refresh()
                .catch((caught) => {
                  setError(caught instanceof Error ? caught.message : "Could not refresh delegations.");
                })
                .finally(() => setLoading(false));
            }}
          >
            {loading ? "Refreshing…" : "Refresh delegation state"}
          </button>
          <p className={styles.summaryNote}>
            Bukmatika does not poll for or invent new work here. This panel reflects durable delegation
            state already created by an approved AI plan.
          </p>
        </article>
      </div>

      <div className={styles.delegationList}>
        <div className={styles.listHeading}>
          <div>
            <span className={styles.label}>Delegations</span>
            <h3>Plan-specific control</h3>
          </div>
          <p>Approve the exact proposal, start it explicitly, and stop it whenever you choose.</p>
        </div>

        {!loading && snapshot?.active_delegations.length === 0 ? (
          <div className={styles.emptyState}>
            <strong>No active delegation state</strong>
            <p>When Bukmatika proposes bounded read-only work, it will appear here before execution.</p>
          </div>
        ) : null}

        {snapshot?.active_delegations.map((delegation) => {
          const delegationBusy = busyAction?.endsWith(`:${delegation.delegation_id}`) ?? false;
          const currentStep = delegation.step_ids[delegation.current_step_index] ?? null;

          return (
            <article className={styles.delegationCard} key={delegation.delegation_id}>
              <div className={styles.delegationHeader}>
                <div>
                  <span
                    className={styles.badge}
                    data-tone={statusTone(delegation.status)}
                  >
                    {statusLabel(delegation.status)}
                  </span>
                  <h4>{delegation.step_ids.map(readableStep).join(" → ")}</h4>
                </div>
                <code title={delegation.delegation_id}>{delegation.delegation_id.slice(0, 8)}</code>
              </div>

              <div className={styles.progressTrack} aria-hidden="true">
                <span
                  style={{
                    width: `${Math.min(
                      100,
                      Math.max(
                        0,
                        ((delegation.step_ids.length - delegation.remaining_steps) /
                          delegation.step_ids.length) *
                          100,
                      ),
                    )}%`,
                  }}
                />
              </div>

              <dl className={styles.delegationFacts}>
                <div>
                  <dt>Current step</dt>
                  <dd>{currentStep ? readableStep(currentStep) : "Not started"}</dd>
                </div>
                <div>
                  <dt>Steps remaining</dt>
                  <dd>
                    {delegation.remaining_steps} / {delegation.step_ids.length}
                  </dd>
                </div>
                <div>
                  <dt>Attempts remaining</dt>
                  <dd>
                    {delegation.remaining_attempts} / {delegation.max_total_attempts}
                  </dd>
                </div>
                <div>
                  <dt>Runtime remaining</dt>
                  <dd>{formatDuration(delegation.remaining_runtime_seconds)}</dd>
                </div>
                <div>
                  <dt>Started</dt>
                  <dd>{formatDate(delegation.started_at)}</dd>
                </div>
                <div>
                  <dt>Plan</dt>
                  <dd title={delegation.plan_id}>{delegation.plan_id.slice(0, 8)}</dd>
                </div>
              </dl>

              <div className={styles.actions}>
                {delegation.status === "proposed" ? (
                  <>
                    <button
                      type="button"
                      className={styles.primaryAction}
                      disabled={controlsDisabled || delegationBusy}
                      onClick={() => void decideDelegation(delegation.delegation_id, "approved")}
                    >
                      {busyAction === `approve:${delegation.delegation_id}` ? "Approving…" : "Approve"}
                    </button>
                    <button
                      type="button"
                      className={styles.secondaryAction}
                      disabled={controlsDisabled || delegationBusy}
                      onClick={() => void decideDelegation(delegation.delegation_id, "rejected")}
                    >
                      {busyAction === `reject:${delegation.delegation_id}` ? "Rejecting…" : "Reject"}
                    </button>
                  </>
                ) : null}

                {delegation.status === "approved" ? (
                  <button
                    type="button"
                    className={styles.primaryAction}
                    disabled={controlsDisabled || delegationBusy || !snapshot.level2_enabled}
                    onClick={() => void startDelegation(delegation.delegation_id)}
                  >
                    {busyAction === `start:${delegation.delegation_id}` ? "Starting…" : "Start delegation"}
                  </button>
                ) : null}

                {delegation.status === "running" ? (
                  <button
                    type="button"
                    className={styles.dangerAction}
                    disabled={controlsDisabled || delegationBusy}
                    onClick={() => void stopDelegation(delegation.delegation_id)}
                  >
                    {busyAction === `stop:${delegation.delegation_id}` ? "Requesting stop…" : "Stop delegation"}
                  </button>
                ) : null}

                {delegation.status === "stop_requested" ? (
                  <span className={styles.stopNotice}>A durable stop has been requested.</span>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

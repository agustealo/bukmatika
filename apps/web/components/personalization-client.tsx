"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "../app/personalization/personalization.module.css";

type PreferenceInfluence = {
  ranking: boolean;
  presentation: boolean;
  automation: boolean;
};

type PreferenceProvenance = {
  event_count: number;
  distinct_entity_count: number;
  event_types: string[];
  first_evidence_at: string | null;
  last_evidence_at: string | null;
};

type PreferenceClaim = {
  claim_id: string;
  key: string;
  value: Record<string, unknown>;
  source: "explicit" | "inferred";
  status: string;
  confidence: number;
  scope_type: string;
  scope_value: string;
  influence: PreferenceInfluence;
  evidence_count: number;
  first_observed_at: string;
  last_reinforced_at: string;
  created_at: string;
  updated_at: string;
  provenance: PreferenceProvenance | null;
};

type ActiveGoal = {
  goal_id: string;
  title: string;
  kind: string;
  status: string;
  created_at: string;
  updated_at: string;
};

type ActivityOutcome = {
  outcome_id: string;
  plan_id: string | null;
  action_decision_id: string | null;
  outcome: string;
  entity_type: string | null;
  entity_id: string | null;
  occurred_at: string;
};

type ActivityItem = {
  decision_id: string;
  plan_id: string;
  plan_status: string;
  user_request: string;
  step_id: string;
  capability: string;
  rationale: string | null;
  decision: string;
  decision_reason: string;
  policy_version: string;
  approval_required: boolean;
  plan_created_at: string;
  evaluated_at: string;
  outcomes: ActivityOutcome[];
  latest_outcome: string | null;
  model_provider: string | null;
  model_name: string | null;
};

type ControlCenterSnapshot = {
  user_model_id: string;
  ai_enabled: boolean;
  learning_enabled: boolean;
  autonomy_level: number;
  explicit_preferences: PreferenceClaim[];
  inferred_preferences: PreferenceClaim[];
  active_goals: ActiveGoal[];
  recent_activity: ActivityItem[];
  recent_outcomes: ActivityOutcome[];
};

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

function readableKey(value: string): string {
  return value
    .split(".")
    .map((part) => part.replaceAll("_", " "))
    .join(" · ");
}

function scopeLabel(claim: PreferenceClaim): string {
  return claim.scope_value ? `${claim.scope_type}: ${claim.scope_value}` : claim.scope_type;
}

function confidenceLabel(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function preferenceValue(claim: PreferenceClaim): string {
  const format = claim.value.format;
  if (typeof format === "string") {
    return format;
  }

  const style = claim.value.style;
  if (typeof style === "string") {
    return style;
  }

  const values = Object.values(claim.value);
  if (values.length === 1) {
    const first = values[0];
    if (typeof first === "string" || typeof first === "number" || typeof first === "boolean") {
      return String(first);
    }
  }

  return JSON.stringify(claim.value);
}

async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      return new Error(payload.detail);
    }
  } catch {
    // The status fallback below remains the canonical message for non-JSON responses.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

export function PersonalizationClient() {
  const [snapshot, setSnapshot] = useState<ControlCenterSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [corrections, setCorrections] = useState<Record<string, string>>({});

  const loadSnapshot = useCallback(async () => {
    const response = await apiFetch("/v1/personalization/control-center");
    if (!response.ok) {
      throw await responseError(response, "Could not load AI & Personalization");
    }
    const payload = (await response.json()) as ControlCenterSnapshot;
    setSnapshot(payload);
  }, []);

  useEffect(() => {
    let active = true;

    void (async () => {
      try {
        await loadSnapshot();
      } catch (caught) {
        if (active) {
          setError(caught instanceof Error ? caught.message : "Could not load personalization.");
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
  }, [loadSnapshot]);

  const inferredEvidenceTotal = useMemo(
    () => snapshot?.inferred_preferences.reduce((total, claim) => total + claim.evidence_count, 0) ?? 0,
    [snapshot],
  );

  async function mutate(action: string, request: () => Promise<Response>) {
    setBusyAction(action);
    setError(null);
    try {
      const response = await request();
      if (!response.ok) {
        throw await responseError(response, "The personalization change was not saved");
      }
      await loadSnapshot();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The personalization change failed.");
    } finally {
      setBusyAction(null);
    }
  }

  async function updateSettings(
    patch: Partial<Pick<ControlCenterSnapshot, "ai_enabled" | "learning_enabled" | "autonomy_level">>,
  ) {
    if (!snapshot) {
      return;
    }
    const body = {
      ai_enabled: patch.ai_enabled ?? snapshot.ai_enabled,
      learning_enabled: patch.learning_enabled ?? snapshot.learning_enabled,
      autonomy_level: patch.autonomy_level ?? snapshot.autonomy_level,
    };
    await mutate("settings", () =>
      apiFetch("/v1/personalization/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
  }

  async function forgetClaim(claim: PreferenceClaim) {
    await mutate(`forget:${claim.claim_id}`, () =>
      apiFetch(`/v1/personalization/preferences/${claim.claim_id}/forget`, {
        method: "POST",
      }),
    );
  }

  async function correctFormatClaim(claim: PreferenceClaim) {
    const current = typeof claim.value.format === "string" ? claim.value.format : "";
    const corrected = (corrections[claim.claim_id] ?? current).trim();
    if (!corrected) {
      setError("Enter the format you want Bukmatika to treat as your explicit preference.");
      return;
    }

    await mutate(`correct:${claim.claim_id}`, () =>
      apiFetch("/v1/personalization/preferences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: claim.key,
          value: { format: corrected },
          scope_type: claim.scope_type,
          scope_value: claim.scope_value,
          influence: claim.influence,
        }),
      }),
    );
    setCorrections((currentValues) => {
      const next = { ...currentValues };
      delete next[claim.claim_id];
      return next;
    });
  }

  if (loading) {
    return <div className="surface-loading">Loading your AI controls and activity…</div>;
  }

  if (!snapshot) {
    return (
      <div className="error-card" role="alert">
        {error ?? "AI & Personalization is unavailable."}
      </div>
    );
  }

  return (
    <section className={styles.surface}>
      <div className={styles.hero}>
        <div>
          <div className="eyebrow">AI & Personalization</div>
          <h1>You stay in the control loop.</h1>
          <p>
            Inspect what Bukmatika has learned, change the boundaries it operates within, and trace
            every persisted AI policy decision without exposing raw private context.
          </p>
        </div>
        <div className={styles.heroStats} aria-label="Personalization summary">
          <div>
            <strong>{snapshot.explicit_preferences.length}</strong>
            <span>explicit</span>
          </div>
          <div>
            <strong>{snapshot.inferred_preferences.length}</strong>
            <span>inferred</span>
          </div>
          <div>
            <strong>{inferredEvidenceTotal}</strong>
            <span>evidence links</span>
          </div>
        </div>
      </div>

      {error ? (
        <div className="error-card" role="alert">
          {error}
        </div>
      ) : null}

      <section className={styles.section} aria-labelledby="control-heading">
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.sectionIndex}>01</span>
            <h2 id="control-heading">Control center</h2>
          </div>
          <p>These switches update the existing principal-owned settings authority.</p>
        </div>

        <div className={styles.controlGrid}>
          <article className={styles.controlCard}>
            <div>
              <h3>AI assistance</h3>
              <p>Disable model-backed orchestration while keeping normal product features usable.</p>
            </div>
            <button
              className={styles.switch}
              type="button"
              role="switch"
              aria-checked={snapshot.ai_enabled}
              disabled={busyAction === "settings"}
              onClick={() => void updateSettings({ ai_enabled: !snapshot.ai_enabled })}
            >
              <span>{snapshot.ai_enabled ? "Enabled" : "Disabled"}</span>
              <i aria-hidden="true" data-on={snapshot.ai_enabled ? "true" : "false"} />
            </button>
          </article>

          <article className={styles.controlCard}>
            <div>
              <h3>Learning</h3>
              <p>Pause future learned-state mutation without deleting existing preferences.</p>
            </div>
            <button
              className={styles.switch}
              type="button"
              role="switch"
              aria-checked={snapshot.learning_enabled}
              disabled={busyAction === "settings"}
              onClick={() => void updateSettings({ learning_enabled: !snapshot.learning_enabled })}
            >
              <span>{snapshot.learning_enabled ? "Learning" : "Paused"}</span>
              <i aria-hidden="true" data-on={snapshot.learning_enabled ? "true" : "false"} />
            </button>
          </article>

          <article className={styles.controlCard}>
            <div>
              <h3>Autonomy</h3>
              <p>Only the shipped Level 0 and Level 1 boundaries are available.</p>
            </div>
            <label className={styles.selectLabel}>
              <span className={styles.visuallyHidden}>Autonomy level</span>
              <select
                value={snapshot.autonomy_level}
                disabled={busyAction === "settings"}
                onChange={(event) =>
                  void updateSettings({ autonomy_level: Number(event.target.value) })
                }
              >
                <option value={0}>Level 0 · Reactive</option>
                <option value={1}>Level 1 · Suggestive</option>
              </select>
            </label>
          </article>
        </div>
      </section>

      <section className={styles.section} aria-labelledby="knowledge-heading">
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.sectionIndex}>02</span>
            <h2 id="knowledge-heading">What Bukmatika knows about me</h2>
          </div>
          <p>Explicit choices are authoritative. Inferences stay separate, inspectable, and reversible.</p>
        </div>

        <div className={styles.preferenceColumns}>
          <PreferenceGroup
            title="Explicit preferences"
            description="Choices you directly set or corrections you made."
            claims={snapshot.explicit_preferences}
            busyAction={busyAction}
            onForget={forgetClaim}
          />
          <PreferenceGroup
            title="Learned inferences"
            description="Evidence-backed patterns Bukmatika inferred from semantic product behavior."
            claims={snapshot.inferred_preferences}
            busyAction={busyAction}
            onForget={forgetClaim}
            corrections={corrections}
            onCorrectionChange={(claimId, value) =>
              setCorrections((current) => ({ ...current, [claimId]: value }))
            }
            onCorrect={correctFormatClaim}
          />
        </div>
      </section>

      <section className={styles.section} aria-labelledby="goals-heading">
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.sectionIndex}>03</span>
            <h2 id="goals-heading">Active goals</h2>
          </div>
          <p>Only goal identity and status are projected here, not raw goal constraints or context blobs.</p>
        </div>
        {snapshot.active_goals.length ? (
          <div className={styles.goalGrid}>
            {snapshot.active_goals.map((goal) => (
              <article className={styles.goalCard} key={goal.goal_id}>
                <span>{goal.kind}</span>
                <h3>{goal.title}</h3>
                <p>Updated {formatDate(goal.updated_at)}</p>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-surface">
            <strong>No active delegated goals</strong>
            <p>Bukmatika has no persisted active goal for this profile.</p>
          </div>
        )}
      </section>

      <section className={styles.section} aria-labelledby="activity-heading">
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.sectionIndex}>04</span>
            <h2 id="activity-heading">AI activity ledger</h2>
          </div>
          <p>Persisted plan and policy evidence only. Missing model/provider data stays missing.</p>
        </div>

        {snapshot.recent_activity.length ? (
          <div className={styles.activityList}>
            {snapshot.recent_activity.map((item) => (
              <article className={styles.activityCard} key={item.decision_id}>
                <div className={styles.activityTopline}>
                  <span className={styles.capability}>{item.capability}</span>
                  <span className={`${styles.decision} ${styles[`decision_${item.decision}`] ?? ""}`}>
                    {item.decision.replaceAll("_", " ")}
                  </span>
                </div>
                <h3>{item.user_request}</h3>
                {item.rationale ? <p className={styles.rationale}>{item.rationale}</p> : null}
                <dl className={styles.activityFacts}>
                  <div>
                    <dt>Policy reason</dt>
                    <dd>{item.decision_reason}</dd>
                  </div>
                  <div>
                    <dt>Approval</dt>
                    <dd>{item.approval_required ? "Required" : "Not required"}</dd>
                  </div>
                  <div>
                    <dt>Outcome</dt>
                    <dd>{item.latest_outcome ?? "No outcome recorded"}</dd>
                  </div>
                  <div>
                    <dt>Evaluated</dt>
                    <dd>{formatDate(item.evaluated_at)}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-surface">
            <strong>No AI activity recorded</strong>
            <p>No persisted plan/action decisions exist for this profile yet.</p>
          </div>
        )}
      </section>

      <section className={styles.section} aria-labelledby="outcomes-heading">
        <div className={styles.sectionHeading}>
          <div>
            <span className={styles.sectionIndex}>05</span>
            <h2 id="outcomes-heading">Recent outcomes</h2>
          </div>
          <p>Feedback history is projected without its raw context payload.</p>
        </div>
        {snapshot.recent_outcomes.length ? (
          <div className={styles.outcomeList}>
            {snapshot.recent_outcomes.map((outcome) => (
              <div className={styles.outcomeRow} key={outcome.outcome_id}>
                <strong>{outcome.outcome}</strong>
                <span>{outcome.entity_type ?? "plan/action"}</span>
                <time dateTime={outcome.occurred_at}>{formatDate(outcome.occurred_at)}</time>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-surface">
            <strong>No outcome history yet</strong>
            <p>Accepted, rejected, corrected, and other persisted outcomes will appear here.</p>
          </div>
        )}
      </section>
    </section>
  );
}

type PreferenceGroupProps = {
  title: string;
  description: string;
  claims: PreferenceClaim[];
  busyAction: string | null;
  onForget: (claim: PreferenceClaim) => Promise<void>;
  corrections?: Record<string, string>;
  onCorrectionChange?: (claimId: string, value: string) => void;
  onCorrect?: (claim: PreferenceClaim) => Promise<void>;
};

function PreferenceGroup({
  title,
  description,
  claims,
  busyAction,
  onForget,
  corrections,
  onCorrectionChange,
  onCorrect,
}: PreferenceGroupProps) {
  return (
    <div className={styles.preferenceGroup}>
      <div className={styles.preferenceGroupHeading}>
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      {claims.length ? (
        <div className={styles.preferenceList}>
          {claims.map((claim) => {
            const forgetBusy = busyAction === `forget:${claim.claim_id}`;
            const correctionBusy = busyAction === `correct:${claim.claim_id}`;
            const canCorrectFormat =
              claim.source === "inferred" &&
              claim.key === "format.preferred" &&
              onCorrectionChange &&
              onCorrect;
            const existingFormat =
              typeof claim.value.format === "string" ? claim.value.format : "";

            return (
              <article className={styles.preferenceCard} key={claim.claim_id}>
                <div className={styles.preferenceTopline}>
                  <span className={styles.preferenceSource}>{claim.source}</span>
                  <span>{scopeLabel(claim)}</span>
                </div>
                <h4>{readableKey(claim.key)}</h4>
                <div className={styles.preferenceValue}>{preferenceValue(claim)}</div>

                <div className={styles.preferenceMeta}>
                  <span>Confidence {confidenceLabel(claim.confidence)}</span>
                  <span>{claim.evidence_count} evidence links</span>
                  <span>Updated {formatDate(claim.updated_at)}</span>
                </div>

                {claim.provenance ? (
                  <div className={styles.provenance}>
                    <strong>Why this exists</strong>
                    <p>
                      {claim.provenance.event_count} linked events across{" "}
                      {claim.provenance.distinct_entity_count} distinct source entities.
                    </p>
                    <p>
                      Signals:{" "}
                      {claim.provenance.event_types.length
                        ? claim.provenance.event_types.join(", ")
                        : "No linked signal type is currently available."}
                    </p>
                    <span>
                      Evidence window: {formatDate(claim.provenance.first_evidence_at)} →{" "}
                      {formatDate(claim.provenance.last_evidence_at)}
                    </span>
                  </div>
                ) : null}

                {canCorrectFormat ? (
                  <div className={styles.correction}>
                    <label>
                      Correct preferred format
                      <input
                        value={corrections?.[claim.claim_id] ?? existingFormat}
                        onChange={(event) => {
                          if (onCorrectionChange) {
                            onCorrectionChange(claim.claim_id, event.target.value);
                          }
                        }}
                      />
                    </label>
                    <button
                      type="button"
                      className="secondary-action"
                      disabled={correctionBusy || (busyAction !== null && !correctionBusy)}
                      onClick={() => {
                        if (onCorrect) {
                          void onCorrect(claim);
                        }
                      }}
                    >
                      {correctionBusy ? "Saving…" : "Make explicit"}
                    </button>
                  </div>
                ) : null}

                <div className={styles.preferenceActions}>
                  <button
                    type="button"
                    className={styles.forgetButton}
                    disabled={forgetBusy || (busyAction !== null && !forgetBusy)}
                    onClick={() => void onForget(claim)}
                  >
                    {forgetBusy ? "Forgetting…" : "Forget"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <div className={styles.preferenceEmpty}>None recorded.</div>
      )}
    </div>
  );
}

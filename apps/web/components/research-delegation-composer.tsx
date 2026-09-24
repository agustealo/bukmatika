"use client";

import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./research-delegation-composer.module.css";

type PlanStep = {
  step_id: string;
  capability: string;
  arguments: Record<string, unknown>;
  rationale: string;
};

type PlanDecision = {
  step_id: string;
  capability: string;
  decision: string;
  reason: string;
  policy_version: string;
};

type PersistedPlan = {
  plan_id: string;
  status: string;
  summary: string;
  steps: PlanStep[];
  decisions: PlanDecision[];
};

type DelegationProposal = {
  delegation_id: string;
  plan_id: string;
  status: string;
  step_ids: string[];
  max_runtime_seconds: number;
  max_retries_per_step: number;
  max_total_attempts: number;
};

type ResearchDelegationComposerProps = {
  libraryEntryIds: string[];
  researchRequest: string;
};

const DELEGATABLE_CAPABILITY = "research.search";

function readableCapability(value: string): string {
  return value.replaceAll(".", " · ").replaceAll("_", " ");
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
      const code = payload.detail.code;
      const message = code.replaceAll("_", " ").toLowerCase();
      return new Error(message.charAt(0).toUpperCase() + message.slice(1));
    }
  } catch {
    // Keep the status-aware fallback for non-JSON responses.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

export function ResearchDelegationComposer({
  libraryEntryIds,
  researchRequest,
}: ResearchDelegationComposerProps) {
  const [plan, setPlan] = useState<PersistedPlan | null>(null);
  const [selectedStepIds, setSelectedStepIds] = useState<string[]>([]);
  const [runtimeSeconds, setRuntimeSeconds] = useState(300);
  const [retriesPerStep, setRetriesPerStep] = useState(0);
  const [proposal, setProposal] = useState<DelegationProposal | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectionKey = libraryEntryIds.join(",");
  useEffect(() => {
    setPlan(null);
    setSelectedStepIds([]);
    setProposal(null);
    setError(null);
  }, [researchRequest, selectionKey]);

  const decisionByStep = useMemo(
    () => new Map(plan?.decisions.map((decision) => [decision.step_id, decision]) ?? []),
    [plan],
  );

  const eligibleStepIds = useMemo(
    () =>
      plan?.steps
        .filter((step) => {
          const decision = decisionByStep.get(step.step_id);
          return step.capability === DELEGATABLE_CAPABILITY && decision?.decision === "allow";
        })
        .map((step) => step.step_id) ?? [],
    [decisionByStep, plan],
  );

  const maxTotalAttempts = selectedStepIds.length * (retriesPerStep + 1);
  const canDraft = researchRequest.trim().length > 0 && libraryEntryIds.length > 0 && !drafting;
  const canPropose =
    plan !== null &&
    proposal === null &&
    selectedStepIds.length > 0 &&
    !proposing &&
    !drafting;

  async function draftPlan() {
    if (!canDraft) return;
    setDrafting(true);
    setError(null);
    setProposal(null);
    try {
      const response = await apiFetch("/v1/ai/plans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_request: researchRequest.trim(),
          context_request: {
            task: "research",
            library_entry_ids: libraryEntryIds,
            scopes: [],
          },
        }),
      });
      if (!response.ok) {
        throw await responseError(response, "Could not draft the research plan");
      }
      const nextPlan = (await response.json()) as PersistedPlan;
      const nextDecisionByStep = new Map(
        nextPlan.decisions.map((decision) => [decision.step_id, decision]),
      );
      const nextEligible = nextPlan.steps
        .filter(
          (step) =>
            step.capability === DELEGATABLE_CAPABILITY &&
            nextDecisionByStep.get(step.step_id)?.decision === "allow",
        )
        .map((step) => step.step_id);
      setPlan(nextPlan);
      setSelectedStepIds(nextEligible);
    } catch (caught) {
      setPlan(null);
      setSelectedStepIds([]);
      setError(caught instanceof Error ? caught.message : "Could not draft the research plan.");
    } finally {
      setDrafting(false);
    }
  }

  async function createProposal() {
    if (!plan || !canPropose) return;
    setProposing(true);
    setError(null);
    try {
      const response = await apiFetch(`/v1/ai/plans/${plan.plan_id}/delegations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          step_ids: selectedStepIds,
          max_runtime_seconds: runtimeSeconds,
          max_retries_per_step: retriesPerStep,
          max_total_attempts: maxTotalAttempts,
        }),
      });
      if (!response.ok) {
        throw await responseError(response, "Could not create the delegation proposal");
      }
      setProposal((await response.json()) as DelegationProposal);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create the delegation proposal.");
    } finally {
      setProposing(false);
    }
  }

  function toggleStep(stepId: string) {
    if (!eligibleStepIds.includes(stepId) || proposal) return;
    setSelectedStepIds((current) =>
      current.includes(stepId)
        ? current.filter((value) => value !== stepId)
        : eligibleStepIds.filter((value) => value === stepId || current.includes(value)),
    );
  }

  return (
    <section className={styles.surface} aria-labelledby="delegated-research-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>Level 2 research</span>
          <h2 id="delegated-research-heading">Turn this question into a bounded plan</h2>
        </div>
        <p>
          Bukmatika can ask your configured model to draft a plan against only the selected books.
          The model cannot approve or start the work. Policy-approved research steps still require
          your exact delegation approval in the AI control center.
        </p>
      </div>

      <div className={styles.contextBar}>
        <div>
          <span>Research request</span>
          <strong>{researchRequest.trim() || "Enter a research question above"}</strong>
        </div>
        <div>
          <span>Selected source context</span>
          <strong>{libraryEntryIds.length} book{libraryEntryIds.length === 1 ? "" : "s"}</strong>
        </div>
        <button type="button" disabled={!canDraft || proposing} onClick={() => void draftPlan()}>
          {drafting ? "Drafting plan…" : plan ? "Redraft plan" : "Draft Level 2 plan"}
        </button>
      </div>

      {error ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}

      {plan ? (
        <div className={styles.planPanel}>
          <header className={styles.planHeading}>
            <div>
              <span>Persisted plan</span>
              <h3>{plan.summary}</h3>
            </div>
            <code>{plan.plan_id}</code>
          </header>

          <div className={styles.steps}>
            {plan.steps.map((step, index) => {
              const decision = decisionByStep.get(step.step_id);
              const eligible = eligibleStepIds.includes(step.step_id);
              const selected = selectedStepIds.includes(step.step_id);
              return (
                <article className={styles.step} key={step.step_id} data-eligible={eligible}>
                  <div className={styles.stepIndex}>{String(index + 1).padStart(2, "0")}</div>
                  <div className={styles.stepBody}>
                    <div className={styles.stepTopline}>
                      <div>
                        <strong>{step.step_id}</strong>
                        <span>{readableCapability(step.capability)}</span>
                      </div>
                      <span
                        className={styles.decision}
                        data-decision={decision?.decision ?? "missing"}
                      >
                        {decision?.decision ?? "No policy decision"}
                      </span>
                    </div>
                    <p>{step.rationale}</p>
                    <small>{decision?.reason ?? "No persisted policy reason is available."}</small>
                    <label className={styles.stepToggle}>
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={!eligible || proposal !== null || proposing}
                        onChange={() => toggleStep(step.step_id)}
                      />
                      <span>
                        {eligible
                          ? "Include this read-only step in the delegation request"
                          : "Not eligible for Level 2 delegation"}
                      </span>
                    </label>
                  </div>
                </article>
              );
            })}
          </div>

          {eligibleStepIds.length === 0 ? (
            <div className={styles.notice}>
              This plan contains no policy-approved <code>research.search</code> steps that can enter
              Level 2. Nothing has been delegated.
            </div>
          ) : (
            <div className={styles.budgetPanel}>
              <div className={styles.budgetHeading}>
                <div>
                  <span>Execution budget</span>
                  <strong>{selectedStepIds.length} selected step{selectedStepIds.length === 1 ? "" : "s"}</strong>
                </div>
                <p>
                  The worker may only consume this exact persisted selection. Changing the plan or
                  budget after approval invalidates its fingerprint.
                </p>
              </div>

              <div className={styles.budgetGrid}>
                <label>
                  Runtime ceiling
                  <select
                    value={runtimeSeconds}
                    disabled={proposal !== null || proposing}
                    onChange={(event) => setRuntimeSeconds(Number(event.target.value))}
                  >
                    <option value={120}>2 minutes</option>
                    <option value={300}>5 minutes</option>
                    <option value={900}>15 minutes</option>
                    <option value={1800}>30 minutes</option>
                  </select>
                </label>
                <label>
                  Retries per step
                  <select
                    value={retriesPerStep}
                    disabled={proposal !== null || proposing}
                    onChange={(event) => setRetriesPerStep(Number(event.target.value))}
                  >
                    <option value={0}>No retries</option>
                    <option value={1}>1 retry</option>
                    <option value={2}>2 retries</option>
                    <option value={3}>3 retries</option>
                  </select>
                </label>
                <div>
                  <span>Total attempt ceiling</span>
                  <strong>{maxTotalAttempts}</strong>
                </div>
              </div>

              {proposal ? (
                <div className={styles.proposalSuccess} role="status">
                  <div>
                    <span>Delegation proposal created</span>
                    <strong>{proposal.delegation_id}</strong>
                    <p>
                      Nothing is running yet. Review the exact proposal, approve it, and start it from
                      the AI control center.
                    </p>
                  </div>
                  <a href="/personalization">Review in AI controls</a>
                </div>
              ) : (
                <button
                  type="button"
                  className={styles.proposeAction}
                  disabled={!canPropose}
                  onClick={() => void createProposal()}
                >
                  {proposing ? "Creating approval request…" : "Create delegation approval request"}
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className={styles.emptyState}>
          <strong>No AI plan has been created.</strong>
          <p>
            Select readable books and enter a research question above. Drafting a plan does not run
            any capability or change your library.
          </p>
        </div>
      )}
    </section>
  );
}

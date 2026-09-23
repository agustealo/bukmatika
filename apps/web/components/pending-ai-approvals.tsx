"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./pending-ai-approvals.module.css";

type PendingApproval = {
  plan_id: string;
  action_decision_id: string;
  step_id: string;
  capability: string;
  arguments: Record<string, unknown>;
  rationale: string;
  user_request: string;
  policy_reason: string;
  policy_version: string;
  step_fingerprint: string;
  evaluated_at: string;
  approval_status: "approved" | "rejected" | null;
};

type PendingResponse = {
  items: PendingApproval[];
};

async function responseError(response: Response, fallback: string): Promise<Error> {
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      "code" in payload.detail &&
      typeof payload.detail.code === "string"
    ) {
      return new Error(payload.detail.code.replaceAll("_", " ").toLowerCase());
    }
    if (typeof payload.detail === "string") {
      return new Error(payload.detail);
    }
  } catch {
    // Fall through to the status-based message.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

function prettyArguments(argumentsValue: Record<string, unknown>): string {
  return JSON.stringify(argumentsValue, null, 2);
}

export function PendingAIApprovals() {
  const [items, setItems] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch("/v1/ai/approvals/pending");
      if (!response.ok) {
        throw await responseError(response, "Could not load pending approvals");
      }
      const payload = (await response.json()) as PendingResponse;
      setItems(payload.items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load pending approvals.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const execute = useCallback(
    async (item: PendingApproval) => {
      const response = await apiFetch(
        `/v1/ai/plans/${encodeURIComponent(item.plan_id)}/steps/${encodeURIComponent(item.step_id)}/execute`,
        { method: "POST" },
      );
      if (!response.ok) {
        throw await responseError(response, "Approved action could not execute");
      }
      setNotice(`${item.capability} was approved and applied.`);
    },
    [],
  );

  const runApproved = useCallback(
    async (item: PendingApproval) => {
      setBusy(item.action_decision_id);
      setError(null);
      setNotice(null);
      try {
        await execute(item);
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Approved action failed.");
      } finally {
        await refresh();
        setBusy(null);
      }
    },
    [execute, refresh],
  );

  const decide = useCallback(
    async (item: PendingApproval, decision: "approved" | "rejected") => {
      setBusy(item.action_decision_id);
      setError(null);
      setNotice(null);
      try {
        const response = await apiFetch(
          `/v1/ai/actions/${encodeURIComponent(item.action_decision_id)}/approval`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision }),
          },
        );
        if (!response.ok) {
          throw await responseError(response, "Could not save approval decision");
        }

        if (decision === "approved") {
          await execute(item);
        } else {
          setNotice(`${item.capability} was rejected and will not execute.`);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "Approval action failed.");
      } finally {
        await refresh();
        setBusy(null);
      }
    },
    [execute, refresh],
  );

  return (
    <section className={styles.surface} aria-labelledby="approval-heading">
      <div className={styles.heading}>
        <div>
          <span className={styles.index}>06</span>
          <div>
            <span className="eyebrow">Approval queue</span>
            <h2 id="approval-heading">Actions waiting for you</h2>
          </div>
        </div>
        <p>
          Bukmatika cannot authorize its own durable actions. Review the exact capability, arguments,
          rationale, and deterministic policy reason before approving or rejecting it.
        </p>
      </div>

      {error ? (
        <div className={styles.error} role="alert">
          {error}
        </div>
      ) : null}
      {notice ? <div className={styles.notice}>{notice}</div> : null}

      {loading ? <div className="surface-loading">Checking approval queue…</div> : null}

      {!loading && items.length === 0 ? (
        <div className="empty-surface">
          <strong>No actions are waiting for approval</strong>
          <p>Durable AI proposals will appear here before they are allowed to change user state.</p>
        </div>
      ) : null}

      {items.length > 0 ? (
        <div className={styles.list}>
          {items.map((item) => {
            const itemBusy = busy === item.action_decision_id;
            const alreadyApproved = item.approval_status === "approved";
            return (
              <article className={styles.card} key={item.action_decision_id}>
                <div className={styles.topline}>
                  <span className={styles.capability}>{item.capability}</span>
                  <time dateTime={item.evaluated_at}>
                    {new Date(item.evaluated_at).toLocaleString()}
                  </time>
                </div>
                <h3>{item.user_request}</h3>
                <p className={styles.rationale}>{item.rationale}</p>

                <div className={styles.policy}>
                  <span>{alreadyApproved ? "Approved, execution pending" : "Policy gate"}</span>
                  <p>
                    {alreadyApproved
                      ? "The durable approval is already saved. Running it still rechecks the exact fingerprint, current AI state, and current policy."
                      : item.policy_reason}
                  </p>
                </div>

                <details className={styles.arguments}>
                  <summary>Exact action arguments</summary>
                  <pre>{prettyArguments(item.arguments)}</pre>
                </details>

                <div className={styles.fingerprint}>
                  <span>Approval fingerprint</span>
                  <code>{item.step_fingerprint}</code>
                </div>

                <div className={styles.actions}>
                  {alreadyApproved ? (
                    <button
                      className={styles.approve}
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void runApproved(item)}
                    >
                      {itemBusy ? "Applying…" : "Run approved action"}
                    </button>
                  ) : (
                    <>
                      <button
                        className={styles.reject}
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void decide(item, "rejected")}
                      >
                        {itemBusy ? "Saving…" : "Reject"}
                      </button>
                      <button
                        className={styles.approve}
                        type="button"
                        disabled={busy !== null}
                        onClick={() => void decide(item, "approved")}
                      >
                        {itemBusy ? "Applying…" : "Approve exact action"}
                      </button>
                    </>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

"use client";

import { useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "../app/personalization/personalization-data-controls.module.css";

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
    // Non-JSON failures fall back to the status-aware message below.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

function downloadJson(payload: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function PersonalizationDataControls() {
  const [confirmation, setConfirmation] = useState("");
  const [accountConfirmation, setAccountConfirmation] = useState("");
  const [busy, setBusy] = useState<
    "export" | "reset" | "account-export" | "account-delete" | null
  >(null);
  const [error, setError] = useState<string | null>(null);

  async function exportPersonalization() {
    setBusy("export");
    setError(null);
    try {
      const response = await apiFetch("/v1/personalization/export");
      if (!response.ok) {
        throw await responseError(response, "Could not export personalization data");
      }
      downloadJson(
        (await response.json()) as unknown,
        `bukmatika-personalization-${new Date().toISOString().slice(0, 10)}.json`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not export personalization data.");
    } finally {
      setBusy(null);
    }
  }

  async function resetPersonalization() {
    if (confirmation !== "RESET") {
      setError('Type "RESET" exactly before deleting your personalization data.');
      return;
    }

    setBusy("reset");
    setError(null);
    try {
      const response = await apiFetch("/v1/personalization/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "RESET" }),
      });
      if (!response.ok) {
        throw await responseError(response, "Could not reset personalization data");
      }
      await response.json();
      window.location.reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not reset personalization data.");
      setBusy(null);
    }
  }

  async function exportAccount() {
    setBusy("account-export");
    setError(null);
    try {
      const response = await apiFetch("/v1/personalization/account/export");
      if (!response.ok) {
        throw await responseError(response, "Could not export account data");
      }
      downloadJson(
        (await response.json()) as unknown,
        `bukmatika-account-data-${new Date().toISOString().slice(0, 10)}.json`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not export account data.");
    } finally {
      setBusy(null);
    }
  }

  async function deleteAccount() {
    if (accountConfirmation !== "DELETE") {
      setError('Type "DELETE" exactly before erasing your Bukmatika account data.');
      return;
    }

    setBusy("account-delete");
    setError(null);
    try {
      const response = await apiFetch("/v1/personalization/account/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "DELETE" }),
      });
      if (!response.ok) {
        throw await responseError(response, "Could not delete account data");
      }
      await response.json();
      window.location.assign("/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not delete account data.");
      setBusy(null);
    }
  }

  return (
    <section className={styles.dataSurface} aria-labelledby="data-controls-heading">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.sectionIndex}>07</span>
          <h2 id="data-controls-heading">Your data and privacy</h2>
        </div>
        <p>
          Export or reset only the AI/personalization layer, or export and erase the complete local
          Bukmatika account separately. These actions have deliberately different confirmation
          boundaries.
        </p>
      </div>

      {error ? (
        <div className="error-card" role="alert">
          {error}
        </div>
      ) : null}

      <div className={styles.dataControlGrid}>
        <article className={styles.exportCard}>
          <div>
            <span className={styles.dataEyebrow}>Personalization portability</span>
            <h3>Export my personalization</h3>
            <p>
              Download your user model, preference claims and evidence references, goals, plans,
              policy decisions, and outcome history as versioned JSON.
            </p>
          </div>
          <button
            type="button"
            className={styles.dataAction}
            disabled={busy !== null}
            onClick={() => void exportPersonalization()}
          >
            {busy === "export" ? "Preparing export…" : "Download personalization JSON"}
          </button>
        </article>

        <article className={styles.resetCard}>
          <div>
            <span className={styles.dangerEyebrow}>Personalization reset</span>
            <h3>Reset what Bukmatika knows about me</h3>
            <p>
              Deletes personalization claims, goals, AI plans, policy decisions, and outcomes. Your
              library, documents, reading progress, bookmarks, highlights, and account remain.
            </p>
          </div>
          <label className={styles.resetConfirmation}>
            Type <strong>RESET</strong> to confirm
            <input
              value={confirmation}
              autoComplete="off"
              spellCheck={false}
              disabled={busy !== null}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <button
            type="button"
            className={styles.resetAction}
            disabled={busy !== null || confirmation !== "RESET"}
            onClick={() => void resetPersonalization()}
          >
            {busy === "reset" ? "Resetting…" : "Reset personalization"}
          </button>
        </article>

        <article className={styles.exportCard}>
          <div>
            <span className={styles.dataEyebrow}>Whole-account portability</span>
            <h3>Export all my Bukmatika data</h3>
            <p>
              Download a versioned account archive containing your library manifest and reading
              state, personalization, acquisition requests, delegated-work records, activity
              history, session metadata, and private local-import provenance. Authentication token
              hashes and worker lease tokens are excluded.
            </p>
          </div>
          <button
            type="button"
            className={styles.dataAction}
            disabled={busy !== null}
            onClick={() => void exportAccount()}
          >
            {busy === "account-export" ? "Preparing account export…" : "Download account JSON"}
          </button>
        </article>

        <article className={styles.resetCard}>
          <div>
            <span className={styles.dangerEyebrow}>Whole-account erasure</span>
            <h3>Delete my Bukmatika account data</h3>
            <p>
              Removes this local profile, sessions, library ownership, reading state and
              annotations, personalization, activity history, acquisition/delegation state, and
              principal-private local imports. Shared catalog truth and bytes still referenced by
              another profile are preserved. This cannot be undone.
            </p>
          </div>
          <label className={styles.resetConfirmation}>
            Type <strong>DELETE</strong> to confirm
            <input
              value={accountConfirmation}
              autoComplete="off"
              spellCheck={false}
              disabled={busy !== null}
              onChange={(event) => setAccountConfirmation(event.target.value)}
            />
          </label>
          <button
            type="button"
            className={styles.resetAction}
            disabled={busy !== null || accountConfirmation !== "DELETE"}
            onClick={() => void deleteAccount()}
          >
            {busy === "account-delete" ? "Deleting account data…" : "Delete account data"}
          </button>
        </article>
      </div>
    </section>
  );
}

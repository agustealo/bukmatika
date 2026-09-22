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
  } catch {
    // Non-JSON failures fall back to the status-aware message below.
  }
  return new Error(`${fallback} (HTTP ${response.status}).`);
}

export function PersonalizationDataControls() {
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState<"export" | "reset" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function exportPersonalization() {
    setBusy("export");
    setError(null);
    try {
      const response = await apiFetch("/v1/personalization/export");
      if (!response.ok) {
        throw await responseError(response, "Could not export personalization data");
      }
      const payload = (await response.json()) as unknown;
      const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `bukmatika-personalization-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
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

  return (
    <section className={styles.dataSurface} aria-labelledby="data-controls-heading">
      <div className={styles.sectionHeading}>
        <div>
          <span className={styles.sectionIndex}>06</span>
          <h2 id="data-controls-heading">Your personalization data</h2>
        </div>
        <p>
          Export the finite AI/personalization record or reset it without touching your library,
          documents, reading progress, bookmarks, or research corpus.
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
            <span className={styles.dataEyebrow}>Portability</span>
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
            {busy === "export" ? "Preparing export…" : "Download JSON export"}
          </button>
        </article>

        <article className={styles.resetCard}>
          <div>
            <span className={styles.dangerEyebrow}>Destructive reset</span>
            <h3>Reset what Bukmatika knows about me</h3>
            <p>
              Deletes personalization claims, goals, AI plans, policy decisions, and outcomes.
              Existing behavior events remain append-only but become ineligible for future learning.
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
      </div>
    </section>
  );
}

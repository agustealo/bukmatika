"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

type LibraryReadiness = {
  entry_count: number;
  readable_entry_count: number;
};

export function FirstRunGuide() {
  const [showGuide, setShowGuide] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function inspectLibrary() {
      try {
        const response = await apiFetch("/v1/library/readiness", { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as LibraryReadiness;
        if (!cancelled) setShowGuide(body.entry_count === 0);
      } catch {
        // Discovery remains the primary surface if onboarding state cannot be loaded.
      }
    }

    void inspectLibrary();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!showGuide) return null;

  return (
    <section className="empty-surface" aria-labelledby="first-run-guide-title">
      <p className="eyebrow">First steps</p>
      <h2 id="first-run-guide-title">Build the first shelf from a question.</h2>
      <p>
        Search for a topic, inspect the work and edition evidence, then save or acquire only the
        source you actually want. Once a readable document is ready, Bukmatika carries that same
        source into the Reader and grounded Research without copying it into a second library.
      </p>
      <ol>
        <li>Search the open-book web below.</li>
        <li>Open a result dossier and choose the work or exact edition you want to keep.</li>
        <li>Read it, annotate it, or select it as grounded Research evidence.</li>
      </ol>
      <p>
        Already have book files? <a href="/library/transfer">Import your local library instead.</a>
      </p>
    </section>
  );
}

"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

type LibraryReadiness = {
  entry_count: number;
  readable_entry_count: number;
};

export function ResearchReadinessGuide() {
  const [readiness, setReadiness] = useState<LibraryReadiness | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function inspectLibrary() {
      try {
        const response = await apiFetch("/v1/library/readiness", { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as LibraryReadiness;
        if (!cancelled) setReadiness(body);
      } catch {
        // The canonical Research surface still owns request/error handling.
      }
    }

    void inspectLibrary();
    return () => {
      cancelled = true;
    };
  }, []);

  if (readiness === null || readiness.entry_count === 0 || readiness.readable_entry_count > 0) {
    return null;
  }

  return (
    <section className="empty-surface" aria-labelledby="research-readiness-title">
      <h2 id="research-readiness-title">Your books are not research-ready yet.</h2>
      <p>
        Bukmatika can ground Research only in processed canonical text. Your library already has
        saved books, but none currently exposes a readable document. Check acquisition and
        processing progress, or import a local supported file if you already have the book.
      </p>
      <p>
        <a href="/status">Check processing status</a> ·{" "}
        <a href="/library/transfer">Import local books</a>
      </p>
    </section>
  );
}

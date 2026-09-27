"use client";

import { useEffect, useState } from "react";

import { apiFetch } from "../lib/api";

type LibraryItem = {
  library_entry_id: string;
  readable_document_id: string | null;
};

type LibraryResponse = {
  items: LibraryItem[];
};

export function ResearchReadinessGuide() {
  const [hasOwnedBooks, setHasOwnedBooks] = useState(false);
  const [hasReadableBooks, setHasReadableBooks] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function inspectLibrary() {
      try {
        const response = await apiFetch("/v1/library", { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as LibraryResponse;
        if (cancelled) return;
        setHasOwnedBooks(body.items.length > 0);
        setHasReadableBooks(body.items.some((item) => item.readable_document_id !== null));
      } catch {
        // The canonical Research surface still owns request/error handling.
      }
    }

    void inspectLibrary();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!hasOwnedBooks || hasReadableBooks) return null;

  return (
    <section className="empty-surface" aria-labelledby="research-readiness-title">
      <strong id="research-readiness-title">Your books are not research-ready yet.</strong>
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

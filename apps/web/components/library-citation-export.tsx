"use client";

import { useState } from "react";

import { apiFetch } from "../lib/api";

type CitationFormat = "bibtex" | "csl-json" | "ris";

const FORMAT_LABELS: Record<CitationFormat, string> = {
  bibtex: "BibTeX (.bib)",
  "csl-json": "CSL JSON (.json)",
  ris: "RIS (.ris)",
};

function fallbackFilename(format: CitationFormat): string {
  if (format === "bibtex") return "bukmatika-library.bib";
  if (format === "csl-json") return "bukmatika-library.csl.json";
  return "bukmatika-library.ris";
}

function responseFilename(response: Response, format: CitationFormat): string {
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="([^"]+)"/i.exec(disposition);
  return match?.[1] ?? fallbackFilename(format);
}

export function LibraryCitationExport() {
  const [format, setFormat] = useState<CitationFormat>("bibtex");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const response = await apiFetch(`/v1/library/citations/${format}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`Citation export failed with HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = responseFilename(response, format);
        anchor.rel = "noopener";
        document.body.append(anchor);
        anchor.click();
        anchor.remove();
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Citation export failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="library-export-card" aria-labelledby="citation-export-heading">
      <div>
        <p className="eyebrow">Research interoperability</p>
        <h2 id="citation-export-heading">Export citations</h2>
        <p>
          Export your principal-owned library metadata from Bukmatika&apos;s canonical catalog.
          Citation files are read-only projections and do not copy book files or change your library.
        </p>
      </div>
      <div className="library-actions">
        <label>
          <span className="sr-only">Citation format</span>
          <select
            aria-label="Citation format"
            value={format}
            onChange={(event) => setFormat(event.target.value as CitationFormat)}
            disabled={busy}
          >
            {(Object.keys(FORMAT_LABELS) as CitationFormat[]).map((value) => (
              <option key={value} value={value}>
                {FORMAT_LABELS[value]}
              </option>
            ))}
          </select>
        </label>
        <button className="secondary-action" type="button" disabled={busy} onClick={() => void download()}>
          {busy ? "Preparing…" : "Download citations"}
        </button>
      </div>
      {error ? <div className="error-card" role="alert">{error}</div> : null}
    </section>
  );
}

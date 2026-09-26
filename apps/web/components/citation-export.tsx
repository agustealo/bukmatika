"use client";

import { useState } from "react";

import { apiFetch } from "../lib/api";

type CitationFormat = "csl-json" | "bibtex" | "ris";

type CitationExportProps = {
  workId: string;
  editionId: string;
  editionTitle: string;
};

const FORMATS: ReadonlyArray<{ format: CitationFormat; label: string }> = [
  { format: "csl-json", label: "CSL JSON" },
  { format: "bibtex", label: "BibTeX" },
  { format: "ris", label: "RIS" },
];

export function CitationExport({ workId, editionId, editionTitle }: CitationExportProps) {
  const [busyFormat, setBusyFormat] = useState<CitationFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(format: CitationFormat) {
    if (busyFormat !== null) return;
    setBusyFormat(format);
    setError(null);
    try {
      const response = await apiFetch(
        `/v1/dossiers/works/${encodeURIComponent(workId)}/editions/${encodeURIComponent(editionId)}/citation?format=${format}`,
        { cache: "no-store" },
      );
      if (!response.ok) {
        throw new Error(`Citation export failed with HTTP ${response.status}.`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition");
      const filename = disposition?.match(/filename="([^"]+)"/)?.[1] ?? `citation-${editionId}.${extension(format)}`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Citation export failed.");
    } finally {
      setBusyFormat(null);
    }
  }

  return (
    <div className="asset-actions" aria-label={`Citation exports for ${editionTitle}`}>
      <span className="source-label">Export citation</span>
      {FORMATS.map(({ format, label }) => (
        <button
          className="secondary-action"
          type="button"
          disabled={busyFormat !== null}
          key={format}
          onClick={() => void download(format)}
        >
          {busyFormat === format ? "Exporting…" : label}
        </button>
      ))}
      {error ? <span role="alert">{error}</span> : null}
    </div>
  );
}

function extension(format: CitationFormat): string {
  if (format === "bibtex") return "bib";
  if (format === "ris") return "ris";
  return "json";
}

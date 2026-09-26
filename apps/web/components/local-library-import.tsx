"use client";

import { useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./library-portability.module.css";

const LOCAL_IMPORT_MEDIA_TYPE = "application/vnd.bukmatika.local-import";
const ACCEPTED_FORMATS = ".pdf,.epub,.txt,.html,.htm,.xhtml,.docx";

type LocalImportResponse = {
  library_entry_id: string;
  work_id: string;
  edition_id: string;
  asset_id: string;
  document_id: string | null;
  title: string;
  authors: string[];
  format: string;
  media_type: string;
  sha256: string;
  byte_size: number;
  idempotent: boolean;
  processing_required: boolean;
  rights_state: "unknown";
  private_retention_only: true;
};

type DocumentResponse = {
  document_id: string;
  asset_id: string;
};

type ImportState = "imported" | "existing" | "processing_required" | "ocr_required" | "failed";

type ImportedFileResult = {
  key: string;
  filename: string;
  title: string;
  state: ImportState;
  detail: string;
  libraryEntryId?: string;
  documentId?: string;
};

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function failedResult(file: File, titleOverride: string, detail: string): ImportedFileResult {
  return {
    key: fileKey(file),
    filename: file.name,
    title: titleOverride.trim() || file.name,
    state: "failed",
    detail,
  };
}

function messageFromResponse(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return fallback;
  const detail = payload.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "detail" in detail && typeof detail.detail === "string") {
    return detail.detail;
  }
  return fallback;
}

async function responsePayload(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

async function importOne(
  file: File,
  options: { titleOverride: string; author: string },
): Promise<ImportedFileResult> {
  try {
    return await importOneRequest(file, options);
  } catch (cause) {
    const detail =
      cause instanceof Error && cause.message.trim()
        ? cause.message
        : "Import request failed before the API returned a response.";
    return failedResult(file, options.titleOverride, detail);
  }
}

async function importOneRequest(
  file: File,
  options: { titleOverride: string; author: string },
): Promise<ImportedFileResult> {
  const { titleOverride, author } = options;
  const metadata = {
    schema_version: 1,
    filename: file.name,
    title: titleOverride.trim() || null,
    author: author.trim() || null,
    reported_media_type: file.type || null,
  };
  const envelope = new Blob([JSON.stringify(metadata), "\n", file], {
    type: LOCAL_IMPORT_MEDIA_TYPE,
  });
  const response = await apiFetch("/v1/library/import/local", {
    method: "POST",
    headers: { "Content-Type": LOCAL_IMPORT_MEDIA_TYPE },
    body: envelope,
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    return failedResult(
      file,
      titleOverride,
      messageFromResponse(payload, `Import failed with HTTP ${response.status}.`),
    );
  }

  const imported = payload as LocalImportResponse;
  if (imported.document_id) {
    return {
      key: fileKey(file),
      filename: file.name,
      title: imported.title,
      state: imported.idempotent ? "existing" : "imported",
      detail: imported.idempotent ? "Already imported and ready to read." : "Imported and ready to read.",
      libraryEntryId: imported.library_entry_id,
      documentId: imported.document_id,
    };
  }

  const processing = await apiFetch(`/v1/assets/${imported.asset_id}/process`, { method: "POST" });
  const processingPayload = await responsePayload(processing);
  if (processing.ok) {
    const document = processingPayload as DocumentResponse;
    return {
      key: fileKey(file),
      filename: file.name,
      title: imported.title,
      state: imported.idempotent ? "existing" : "imported",
      detail: imported.idempotent
        ? "Already imported; processing is now complete."
        : "Imported and processed into the Reader.",
      libraryEntryId: imported.library_entry_id,
      documentId: document.document_id,
    };
  }

  const detail = messageFromResponse(
    processingPayload,
    `The book was imported, but processing failed with HTTP ${processing.status}.`,
  );
  const serialized = JSON.stringify(processingPayload ?? {});
  if (processing.status === 422 && serialized.includes("DOCUMENT_REQUIRES_OCR")) {
    return {
      key: fileKey(file),
      filename: file.name,
      title: imported.title,
      state: "ocr_required",
      detail: "Imported successfully. This scan needs OCR before it can open in the Reader.",
      libraryEntryId: imported.library_entry_id,
    };
  }
  return {
    key: fileKey(file),
    filename: file.name,
    title: imported.title,
    state: "processing_required",
    detail,
    libraryEntryId: imported.library_entry_id,
  };
}

export function LocalLibraryImport() {
  const [files, setFiles] = useState<File[]>([]);
  const [author, setAuthor] = useState("");
  const [titleOverride, setTitleOverride] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ImportedFileResult[]>([]);

  const totalBytes = useMemo(() => files.reduce((total, file) => total + file.size, 0), [files]);

  async function runImport() {
    if (files.length === 0 || running) return;
    setRunning(true);
    setResults([]);
    const completed: ImportedFileResult[] = [];
    try {
      for (const file of files) {
        const result = await importOne(file, {
          titleOverride: files.length === 1 ? titleOverride : "",
          author,
        });
        completed.push(result);
        setResults([...completed]);
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className={styles.localImport} aria-labelledby="local-import-heading">
      <div className={styles.localImportHeader}>
        <div>
          <span className={styles.step}>01</span>
          <h2 id="local-import-heading">Import local books</h2>
          <p>
            Bring books you already possess into your private Bukmatika library. Local possession
            does not change copyright or license status, so imported bytes stay private and are not
            granted export or sharing rights.
          </p>
        </div>
        <span className={styles.privateBadge}>Private retention</span>
      </div>

      <label className={styles.filePicker}>
        <span>Select books</span>
        <input
          data-testid="local-library-files"
          type="file"
          accept={ACCEPTED_FORMATS}
          multiple
          disabled={running}
          onChange={(event) => {
            setFiles(Array.from(event.currentTarget.files ?? []));
            setResults([]);
            setTitleOverride("");
          }}
        />
      </label>

      <div className={styles.localMetadataGrid}>
        <label>
          <span>Author for this batch <small>optional</small></span>
          <input
            value={author}
            disabled={running}
            maxLength={300}
            onChange={(event) => setAuthor(event.currentTarget.value)}
            placeholder="Author name"
          />
        </label>
        <label>
          <span>Title override <small>{files.length === 1 ? "optional" : "single file only"}</small></span>
          <input
            value={titleOverride}
            disabled={running || files.length !== 1}
            maxLength={500}
            onChange={(event) => setTitleOverride(event.currentTarget.value)}
            placeholder={files.length === 1 ? "Use filename when blank" : "Select one file to override"}
          />
        </label>
      </div>

      {files.length > 0 ? (
        <div className={styles.localSelection} aria-live="polite">
          <strong>{files.length} selected</strong>
          <span>{Math.max(1, Math.round(totalBytes / 1024))} KB total</span>
          <ul>
            {files.map((file) => (
              <li key={fileKey(file)}>
                <span>{file.name}</span>
                <small>{Math.max(1, Math.round(file.size / 1024))} KB</small>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <button type="button" disabled={files.length === 0 || running} onClick={() => void runImport()}>
        {running ? "Importing…" : `Import selected book${files.length === 1 ? "" : "s"}`}
      </button>

      {results.length > 0 ? (
        <div className={styles.localResults} aria-live="polite">
          {results.map((result) => (
            <article key={result.key} data-state={result.state}>
              <div>
                <strong>{result.title}</strong>
                <span>{result.filename}</span>
              </div>
              <p>{result.detail}</p>
              <div className={styles.localResultActions}>
                {result.libraryEntryId && result.documentId ? (
                  <a href={`/read/${result.libraryEntryId}/${result.documentId}`}>Open in Reader</a>
                ) : null}
                {result.libraryEntryId && !result.documentId ? <a href="/status">Open Status Center</a> : null}
              </div>
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

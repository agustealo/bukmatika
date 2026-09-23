"use client";

import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import type { ReaderLocator } from "./reader-annotations";
import styles from "./reader-format-navigation.module.css";

type ReaderNavigationKind = "pdf_pages" | "epub_spine";

type ReaderNavigationItem = {
  key: string;
  label: string;
  section_id: string;
  section_ordinal: number;
  locator: ReaderLocator;
  heading: string | null;
};

type ReaderNavigationResponse = {
  format: string;
  kind: ReaderNavigationKind | null;
  items: ReaderNavigationItem[];
};

type ReaderFormatNavigationProps = {
  libraryEntryId: string;
  documentId: string;
  activeLocator: ReaderLocator | null;
};

export function ReaderFormatNavigation({
  libraryEntryId,
  documentId,
  activeLocator,
}: ReaderFormatNavigationProps) {
  const [navigation, setNavigation] = useState<ReaderNavigationResponse | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setFailed(false);
      try {
        const response = await apiFetch(
          `/v1/library/${libraryEntryId}/documents/${documentId}/navigation`,
          { cache: "no-store" },
        );
        if (!response.ok) {
          throw new Error(`Reader navigation failed with HTTP ${response.status}.`);
        }
        const payload = (await response.json()) as ReaderNavigationResponse;
        if (!cancelled) setNavigation(payload);
      } catch {
        if (!cancelled) setFailed(true);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [documentId, libraryEntryId]);

  const activeKey = useMemo(
    () => currentNavigationKey(navigation?.kind ?? null, activeLocator),
    [activeLocator, navigation?.kind],
  );

  if (failed) {
    return (
      <div className={`reader-sidebar-card ${styles.card}`}>
        <span className="reader-meta-label">Format navigation</span>
        <p>Page or chapter navigation is temporarily unavailable.</p>
      </div>
    );
  }

  if (!navigation || navigation.kind === null || navigation.items.length === 0) return null;

  const label = navigation.kind === "pdf_pages" ? "Pages" : "Contents";
  const help =
    navigation.kind === "pdf_pages"
      ? "Jump between PDF pages that contain canonical processed text."
      : "Jump between canonical EPUB spine sections.";

  function jump(key: string) {
    const item = navigation?.items.find((candidate) => candidate.key === key);
    if (!item) return;
    const params = new URLSearchParams(window.location.search);
    params.set("section", String(item.section_ordinal));
    const query = params.toString();
    const target = `${window.location.pathname}${query ? `?${query}` : ""}`;
    window.location.assign(`${target}#reader-section-${item.section_id}`);
  }

  return (
    <div className={`reader-sidebar-card ${styles.card}`}>
      <span className="reader-meta-label">{label}</span>
      <label className={styles.label} htmlFor="reader-format-navigation">
        {navigation.kind === "pdf_pages" ? "Go to page" : "Go to section"}
      </label>
      <select
        id="reader-format-navigation"
        className={styles.select}
        value={activeKey ?? ""}
        onChange={(event) => jump(event.target.value)}
      >
        <option value="" disabled>
          Select…
        </option>
        {navigation.items.map((item) => (
          <option key={item.key} value={item.key}>
            {item.label}
          </option>
        ))}
      </select>
      <p className={styles.help}>{help}</p>
      <span className={styles.count}>
        {navigation.items.length} {navigation.kind === "pdf_pages" ? "pages" : "sections"}
      </span>
    </div>
  );
}

function currentNavigationKey(
  kind: ReaderNavigationKind | null,
  locator: ReaderLocator | null,
): string | null {
  if (!kind || !locator) return null;
  if (kind === "pdf_pages") {
    const page = positiveInteger(locator.page);
    return page === null ? null : `page:${page}`;
  }
  const spine = positiveInteger(locator.spine);
  return spine === null ? null : `spine:${spine}`;
}

function positiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 1 ? value : null;
}

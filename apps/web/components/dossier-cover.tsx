"use client";

import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./dossier-cover.module.css";

type DossierCoverProps =
  | { workId: string; provider?: never; recordId?: never }
  | { workId?: never; provider: string; recordId: string };

export function DossierCover(props: DossierCoverProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [available, setAvailable] = useState(true);

  const coverPath = useMemo(() => {
    if (props.workId) return `/v1/covers/works/${props.workId}`;
    const params = new URLSearchParams({
      provider: props.provider,
      record_id: props.recordId,
    });
    return `/v1/covers/source?${params.toString()}`;
  }, [props]);

  useEffect(() => {
    const controller = new AbortController();
    let currentObjectUrl: string | null = null;
    setObjectUrl(null);
    setAvailable(true);

    async function load() {
      try {
        const response = await apiFetch(coverPath, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.status === 404 || response.status === 502) {
          setAvailable(false);
          return;
        }
        if (!response.ok) {
          setAvailable(false);
          return;
        }
        const blob = await response.blob();
        if (blob.type !== "image/png" || blob.size === 0) {
          setAvailable(false);
          return;
        }
        currentObjectUrl = URL.createObjectURL(blob);
        setObjectUrl(currentObjectUrl);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          setAvailable(false);
        }
      }
    }

    void load();
    return () => {
      controller.abort();
      if (currentObjectUrl !== null) URL.revokeObjectURL(currentObjectUrl);
    };
  }, [coverPath]);

  if (!available) return null;

  return (
    <section className={styles.surface} aria-label="Book cover">
      {objectUrl ? (
        <img
          className={styles.image}
          data-testid="dossier-cover-image"
          src={objectUrl}
          alt="Canonical book cover"
        />
      ) : (
        <div className={styles.skeleton} aria-label="Loading book cover" aria-busy="true" />
      )}
    </section>
  );
}

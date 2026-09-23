"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../lib/api";
import {
  LibraryItemOrganizer,
  LibraryOrganizationToolbar,
  type CollectionSummary,
  type LibraryFilters,
  type LibraryOrganization,
  type SmartShelfInput,
  type TagSummary,
} from "./library-organization";
import "./library-organization.module.css";

type LibraryItem = {
  library_entry_id: string;
  work_id: string;
  edition_id: string | null;
  title: string;
  authors: string[];
  status: string;
  readable_document_id: string | null;
  readable_format: string | null;
  progress_fraction: number | null;
  reading_status: string | null;
  collections: CollectionSummary[];
  tags: TagSummary[];
};

type LibraryResponse = {
  items: LibraryItem[];
};

type SmartShelfContentsResponse = {
  shelf: LibraryOrganization["smart_shelves"][number];
  items: LibraryItem[];
};

const EMPTY_ORGANIZATION: LibraryOrganization = {
  collections: [],
  tags: [],
  smart_shelves: [],
};
const INITIAL_FILTERS: LibraryFilters = { reading: "all", collectionId: "", tagId: "" };

function libraryUrl(filters: LibraryFilters): string {
  const params = new URLSearchParams();
  if (filters.reading !== "all") params.set("reading_status", filters.reading);
  if (filters.collectionId) params.set("collection_id", filters.collectionId);
  if (filters.tagId) params.set("tag_id", filters.tagId);
  const query = params.toString();
  return query ? `/v1/library?${query}` : "/v1/library";
}

export function LibraryClient() {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [organization, setOrganization] = useState<LibraryOrganization>(EMPTY_ORGANIZATION);
  const [filters, setFilters] = useState<LibraryFilters>(INITIAL_FILTERS);
  const [activeSmartShelfId, setActiveSmartShelfId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadLibrary = useCallback(async (activeFilters: LibraryFilters) => {
    const response = await apiFetch(libraryUrl(activeFilters), { cache: "no-store" });
    if (!response.ok) throw new Error(`Library failed with HTTP ${response.status}.`);
    const body = (await response.json()) as LibraryResponse;
    setItems(body.items);
  }, []);

  const loadSmartShelf = useCallback(async (smartShelfId: string) => {
    const response = await apiFetch(`/v1/library/smart-shelves/${smartShelfId}`, {
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`Smart shelf failed with HTTP ${response.status}.`);
    const body = (await response.json()) as SmartShelfContentsResponse;
    setItems(body.items);
  }, []);

  const loadOrganization = useCallback(async () => {
    const response = await apiFetch("/v1/library/organization", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Library organization failed with HTTP ${response.status}.`);
    }
    setOrganization((await response.json()) as LibraryOrganization);
  }, []);

  const refresh = useCallback(
    async (
      activeFilters: LibraryFilters = filters,
      smartShelfId: string = activeSmartShelfId,
    ) => {
      await Promise.all([
        smartShelfId ? loadSmartShelf(smartShelfId) : loadLibrary(activeFilters),
        loadOrganization(),
      ]);
    },
    [activeSmartShelfId, filters, loadLibrary, loadOrganization, loadSmartShelf],
  );

  useEffect(() => {
    let cancelled = false;
    async function run() {
      setLoading(true);
      setError(null);
      try {
        const libraryRequest = activeSmartShelfId
          ? apiFetch(`/v1/library/smart-shelves/${activeSmartShelfId}`, { cache: "no-store" })
          : apiFetch(libraryUrl(filters), { cache: "no-store" });
        const [libraryResponse, organizationResponse] = await Promise.all([
          libraryRequest,
          apiFetch("/v1/library/organization", { cache: "no-store" }),
        ]);
        if (!libraryResponse.ok) {
          throw new Error(
            activeSmartShelfId
              ? `Smart shelf failed with HTTP ${libraryResponse.status}.`
              : `Library failed with HTTP ${libraryResponse.status}.`,
          );
        }
        if (!organizationResponse.ok) {
          throw new Error(`Library organization failed with HTTP ${organizationResponse.status}.`);
        }
        const libraryBody = (await libraryResponse.json()) as LibraryResponse | SmartShelfContentsResponse;
        const organizationBody = (await organizationResponse.json()) as LibraryOrganization;
        if (!cancelled) {
          setItems("items" in libraryBody ? libraryBody.items : []);
          setOrganization(organizationBody);
        }
      } catch (caught) {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Library failed.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [activeSmartShelfId, filters]);

  function changeFilters(nextFilters: LibraryFilters) {
    setActiveSmartShelfId("");
    setFilters(nextFilters);
  }

  function openSmartShelf(smartShelfId: string) {
    setFilters(INITIAL_FILTERS);
    setActiveSmartShelfId(smartShelfId);
  }

  async function mutation(
    key: string,
    url: string,
    options: RequestInit = { method: "POST" },
    nextFilters: LibraryFilters = filters,
    nextSmartShelfId: string = activeSmartShelfId,
  ): Promise<boolean> {
    if (busyKey !== null) return false;
    setBusyKey(key);
    setError(null);
    try {
      const response = await apiFetch(url, options);
      if (!response.ok) throw new Error(`Library update failed with HTTP ${response.status}.`);
      await refresh(nextFilters, nextSmartShelfId);
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Library update failed.");
      return false;
    } finally {
      setBusyKey(null);
    }
  }

  async function createCollection(name: string): Promise<boolean> {
    return mutation("collection:create", "/v1/library/collections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: null }),
    });
  }

  async function deleteCollection(collectionId: string) {
    const activeShelf = organization.smart_shelves.find(
      (shelf) => shelf.smart_shelf_id === activeSmartShelfId,
    );
    const removesActiveShelf = activeShelf?.rule.collection_id === collectionId;
    const nextFilters =
      filters.collectionId === collectionId ? { ...filters, collectionId: "" } : filters;
    const nextSmartShelfId = removesActiveShelf ? "" : activeSmartShelfId;
    if (nextFilters !== filters) setFilters(nextFilters);
    if (removesActiveShelf) setActiveSmartShelfId("");
    await mutation(
      `collection:${collectionId}`,
      `/v1/library/collections/${collectionId}/remove`,
      { method: "POST" },
      nextFilters,
      nextSmartShelfId,
    );
  }

  async function deleteTag(tagId: string) {
    const activeShelf = organization.smart_shelves.find(
      (shelf) => shelf.smart_shelf_id === activeSmartShelfId,
    );
    const removesActiveShelf = activeShelf?.rule.tag_id === tagId;
    const nextFilters = filters.tagId === tagId ? { ...filters, tagId: "" } : filters;
    const nextSmartShelfId = removesActiveShelf ? "" : activeSmartShelfId;
    if (nextFilters !== filters) setFilters(nextFilters);
    if (removesActiveShelf) setActiveSmartShelfId("");
    await mutation(
      `tag:${tagId}`,
      `/v1/library/tags/${tagId}/remove`,
      { method: "POST" },
      nextFilters,
      nextSmartShelfId,
    );
  }

  async function createSmartShelf(input: SmartShelfInput): Promise<boolean> {
    return mutation("smart-shelf:create", "/v1/library/smart-shelves", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    });
  }

  async function updateSmartShelf(
    smartShelfId: string,
    input: SmartShelfInput,
  ): Promise<boolean> {
    return mutation(
      `smart-shelf:${smartShelfId}`,
      `/v1/library/smart-shelves/${smartShelfId}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    );
  }

  async function deleteSmartShelf(smartShelfId: string) {
    const removesActiveShelf = activeSmartShelfId === smartShelfId;
    const nextSmartShelfId = removesActiveShelf ? "" : activeSmartShelfId;
    if (removesActiveShelf) setActiveSmartShelfId("");
    await mutation(
      `smart-shelf:${smartShelfId}`,
      `/v1/library/smart-shelves/${smartShelfId}/remove`,
      { method: "POST" },
      filters,
      nextSmartShelfId,
    );
  }

  async function toggleCollection(entryId: string, collectionId: string, assigned: boolean) {
    const suffix = assigned ? "/remove" : "";
    await mutation(
      `${entryId}:collection:${collectionId}`,
      `/v1/library/collections/${collectionId}/entries/${entryId}${suffix}`,
    );
  }

  async function assignTag(entryId: string, name: string): Promise<boolean> {
    return mutation(`${entryId}:tag:create`, `/v1/library/entries/${entryId}/tags`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
  }

  async function removeTag(entryId: string, tagId: string) {
    await mutation(
      `${entryId}:tag:${tagId}`,
      `/v1/library/entries/${entryId}/tags/${tagId}/remove`,
    );
  }

  if (loading) {
    return <div className="surface-loading" aria-busy="true">Opening your library…</div>;
  }

  const filtersActive =
    Boolean(activeSmartShelfId) ||
    filters.reading !== "all" ||
    Boolean(filters.collectionId) ||
    Boolean(filters.tagId);
  const activeSmartShelf = organization.smart_shelves.find(
    (shelf) => shelf.smart_shelf_id === activeSmartShelfId,
  );

  return (
    <section className="library-surface" aria-label="Personal library">
      <div className="surface-heading">
        <div>
          <p className="eyebrow">Your library</p>
          <h1>{activeSmartShelf ? activeSmartShelf.name : "Books you have chosen to keep."}</h1>
          <p>
            {activeSmartShelf
              ? "This smart shelf is recomputed live from canonical reading state, collections, and tags."
              : "Reading state, collections, tags, and smart shelves organize canonical library entries without copying the catalog into another shelf system."}
          </p>
        </div>
        <button
          className="secondary-action"
          type="button"
          onClick={() => {
            setError(null);
            void refresh().catch((caught: unknown) => {
              setError(caught instanceof Error ? caught.message : "Library refresh failed.");
            });
          }}
        >
          Refresh
        </button>
      </div>

      <LibraryOrganizationToolbar
        organization={organization}
        filters={filters}
        activeSmartShelfId={activeSmartShelfId}
        busyKey={busyKey}
        onFiltersChange={changeFilters}
        onOpenSmartShelf={openSmartShelf}
        onCreateCollection={createCollection}
        onDeleteCollection={deleteCollection}
        onDeleteTag={deleteTag}
        onCreateSmartShelf={createSmartShelf}
        onUpdateSmartShelf={updateSmartShelf}
        onDeleteSmartShelf={deleteSmartShelf}
      />

      {error ? <div className="error-card" role="alert">{error}</div> : null}

      {items.length === 0 ? (
        <div className="empty-surface">
          <strong>{filtersActive ? "No books match this shelf." : "Your shelves are empty."}</strong>
          <p>
            {filtersActive
              ? "Change the smart shelf rule or manual filters to widen this live view."
              : "Discover a work, inspect its editions, then save the work or the exact edition you want."}
          </p>
          {filtersActive ? (
            <button
              className="secondary-action"
              type="button"
              onClick={() => changeFilters(INITIAL_FILTERS)}
            >
              Show all books
            </button>
          ) : (
            <a className="primary-action link-button" href="/">Discover books</a>
          )}
        </div>
      ) : (
        <div className="library-grid">
          {items.map((item) => {
            const progress = Math.round((item.progress_fraction ?? 0) * 100);
            const readingLabel = item.reading_status ?? "unread";
            return (
              <article className="library-card" key={item.library_entry_id}>
                <div className="book-card-topline">
                  <span className="saved-chip">{item.edition_id ? "Edition" : "Work"}</span>
                  {item.readable_format ? <span className="source-label">{item.readable_format}</span> : null}
                </div>
                <h2>{item.title}</h2>
                <p className="byline">
                  {item.authors.length > 0 ? item.authors.join(", ") : "Unknown author"}
                </p>
                <div className="library-tag-chips" aria-label="Book organization labels">
                  {item.collections.map((collection) => (
                    <span className="saved-chip" key={collection.collection_id}>{collection.name}</span>
                  ))}
                  {item.tags.map((tag) => (
                    <span className="source-label" key={tag.tag_id}>#{tag.name}</span>
                  ))}
                </div>
                <div className="library-state">
                  {item.readable_document_id ? (
                    <>
                      <span>{readingLabel}</span>
                      <span>{progress}%</span>
                    </>
                  ) : (
                    <span>{readingLabel} · readable document not ready yet</span>
                  )}
                </div>
                <div className="library-actions">
                  {item.readable_document_id ? (
                    <a
                      className="primary-action link-button"
                      href={`/read/${item.library_entry_id}/${item.readable_document_id}`}
                    >
                      {progress > 0 ? "Continue reading" : "Read"}
                    </a>
                  ) : null}
                  <a className="secondary-link" href={`/dossier?work_id=${item.work_id}`}>
                    Details
                  </a>
                </div>
                <LibraryItemOrganizer
                  entryId={item.library_entry_id}
                  collections={item.collections}
                  tags={item.tags}
                  organization={organization}
                  busyKey={busyKey}
                  onToggleCollection={(collectionId, assigned) =>
                    toggleCollection(item.library_entry_id, collectionId, assigned)
                  }
                  onAssignTag={(name) => assignTag(item.library_entry_id, name)}
                  onRemoveTag={(tagId) => removeTag(item.library_entry_id, tagId)}
                />
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

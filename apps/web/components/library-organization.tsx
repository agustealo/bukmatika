"use client";

import { FormEvent, useState } from "react";

export type CollectionSummary = {
  collection_id: string;
  name: string;
};

export type TagSummary = {
  tag_id: string;
  name: string;
};

export type LibraryCollection = CollectionSummary & {
  description: string | null;
  item_count: number;
};

export type LibraryTag = TagSummary & {
  item_count: number;
};

export type ReadingFilter = "all" | "unread" | "reading" | "finished";
export type SmartShelfReadingFilter = Exclude<ReadingFilter, "all">;

export type SmartShelfRule = {
  reading_status: SmartShelfReadingFilter | null;
  collection_id: string | null;
  tag_id: string | null;
};

export type LibrarySmartShelf = {
  smart_shelf_id: string;
  name: string;
  description: string | null;
  rule: SmartShelfRule;
  item_count: number;
};

export type LibraryOrganization = {
  collections: LibraryCollection[];
  tags: LibraryTag[];
  smart_shelves: LibrarySmartShelf[];
};

export type LibraryFilters = {
  reading: ReadingFilter;
  collectionId: string;
  tagId: string;
};

export type SmartShelfInput = {
  name: string;
  description: null;
  rule: SmartShelfRule;
};

type ToolbarProps = {
  organization: LibraryOrganization;
  filters: LibraryFilters;
  activeSmartShelfId: string;
  busyKey: string | null;
  onFiltersChange: (filters: LibraryFilters) => void;
  onOpenSmartShelf: (smartShelfId: string) => void;
  onCreateCollection: (name: string) => Promise<boolean>;
  onDeleteCollection: (collectionId: string) => Promise<void>;
  onDeleteTag: (tagId: string) => Promise<void>;
  onCreateSmartShelf: (input: SmartShelfInput) => Promise<boolean>;
  onUpdateSmartShelf: (smartShelfId: string, input: SmartShelfInput) => Promise<boolean>;
  onDeleteSmartShelf: (smartShelfId: string) => Promise<void>;
};

export function LibraryOrganizationToolbar({
  organization,
  filters,
  activeSmartShelfId,
  busyKey,
  onFiltersChange,
  onOpenSmartShelf,
  onCreateCollection,
  onDeleteCollection,
  onDeleteTag,
  onCreateSmartShelf,
  onUpdateSmartShelf,
  onDeleteSmartShelf,
}: ToolbarProps) {
  const [collectionName, setCollectionName] = useState("");
  const [smartShelfName, setSmartShelfName] = useState("");
  const [smartReading, setSmartReading] = useState<ReadingFilter>("all");
  const [smartCollectionId, setSmartCollectionId] = useState("");
  const [smartTagId, setSmartTagId] = useState("");
  const [editingSmartShelfId, setEditingSmartShelfId] = useState("");

  async function submitCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = collectionName.trim();
    if (!normalized || busyKey !== null) return;
    if (await onCreateCollection(normalized)) setCollectionName("");
  }

  function resetSmartShelfForm() {
    setSmartShelfName("");
    setSmartReading("all");
    setSmartCollectionId("");
    setSmartTagId("");
    setEditingSmartShelfId("");
  }

  function useCurrentFilters() {
    setSmartReading(filters.reading);
    setSmartCollectionId(filters.collectionId);
    setSmartTagId(filters.tagId);
  }

  function editSmartShelf(shelf: LibrarySmartShelf) {
    setEditingSmartShelfId(shelf.smart_shelf_id);
    setSmartShelfName(shelf.name);
    setSmartReading(shelf.rule.reading_status ?? "all");
    setSmartCollectionId(shelf.rule.collection_id ?? "");
    setSmartTagId(shelf.rule.tag_id ?? "");
  }

  async function submitSmartShelf(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = smartShelfName.trim();
    const hasRule = smartReading !== "all" || Boolean(smartCollectionId) || Boolean(smartTagId);
    if (!name || !hasRule || busyKey !== null) return;
    const input: SmartShelfInput = {
      name,
      description: null,
      rule: {
        reading_status: smartReading === "all" ? null : smartReading,
        collection_id: smartCollectionId || null,
        tag_id: smartTagId || null,
      },
    };
    const saved = editingSmartShelfId
      ? await onUpdateSmartShelf(editingSmartShelfId, input)
      : await onCreateSmartShelf(input);
    if (saved) resetSmartShelfForm();
  }

  const hasSmartShelfDraft =
    smartReading !== "all" || Boolean(smartCollectionId) || Boolean(smartTagId);
  const currentFiltersCanSave =
    filters.reading !== "all" || Boolean(filters.collectionId) || Boolean(filters.tagId);

  return (
    <section className="library-organize-toolbar" aria-label="Library organization">
      {organization.smart_shelves.length > 0 ? (
        <div className="library-smart-shelf-row" aria-label="Smart shelves">
          <span>Smart shelves</span>
          <div>
            {organization.smart_shelves.map((shelf) => (
              <button
                type="button"
                aria-pressed={activeSmartShelfId === shelf.smart_shelf_id}
                className={activeSmartShelfId === shelf.smart_shelf_id ? "is-active" : ""}
                key={shelf.smart_shelf_id}
                onClick={() => onOpenSmartShelf(shelf.smart_shelf_id)}
              >
                {shelf.name} · {shelf.item_count}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="library-filter-group" role="group" aria-label="Reading status filter">
        {(["all", "unread", "reading", "finished"] as ReadingFilter[]).map((value) => (
          <button
            type="button"
            aria-pressed={filters.reading === value && !activeSmartShelfId}
            className={filters.reading === value && !activeSmartShelfId ? "is-active" : ""}
            key={value}
            onClick={() => onFiltersChange({ ...filters, reading: value })}
          >
            {value[0]?.toUpperCase()}{value.slice(1)}
          </button>
        ))}
      </div>

      <div className="library-filter-selects">
        <label>
          <span>Collection</span>
          <select
            value={filters.collectionId}
            onChange={(event) =>
              onFiltersChange({ ...filters, collectionId: event.target.value })
            }
          >
            <option value="">All collections</option>
            {organization.collections.map((collection) => (
              <option value={collection.collection_id} key={collection.collection_id}>
                {collection.name} ({collection.item_count})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Tag</span>
          <select
            value={filters.tagId}
            onChange={(event) => onFiltersChange({ ...filters, tagId: event.target.value })}
          >
            <option value="">All tags</option>
            {organization.tags.map((tag) => (
              <option value={tag.tag_id} key={tag.tag_id}>
                {tag.name} ({tag.item_count})
              </option>
            ))}
          </select>
        </label>
      </div>

      <details className="library-manage-organizing">
        <summary>Manage shelves & tags</summary>
        <form className="library-create-collection" onSubmit={submitCollection}>
          <input
            aria-label="New collection name"
            maxLength={120}
            placeholder="New collection"
            value={collectionName}
            onChange={(event) => setCollectionName(event.target.value)}
          />
          <button type="submit" disabled={!collectionName.trim() || busyKey !== null}>
            Add collection
          </button>
        </form>

        <form className="library-smart-shelf-form" onSubmit={submitSmartShelf}>
          <div className="library-smart-shelf-form-heading">
            <strong>{editingSmartShelfId ? "Edit smart shelf" : "New smart shelf"}</strong>
            <button
              type="button"
              disabled={!currentFiltersCanSave || busyKey !== null}
              onClick={useCurrentFilters}
            >
              Use current filters
            </button>
          </div>
          <input
            aria-label="Smart shelf name"
            maxLength={120}
            placeholder="Smart shelf name"
            value={smartShelfName}
            onChange={(event) => setSmartShelfName(event.target.value)}
          />
          <div className="library-smart-shelf-rule-grid">
            <label>
              <span>Reading state</span>
              <select
                value={smartReading}
                onChange={(event) => setSmartReading(event.target.value as ReadingFilter)}
              >
                <option value="all">Any state</option>
                <option value="unread">Unread</option>
                <option value="reading">Reading</option>
                <option value="finished">Finished</option>
              </select>
            </label>
            <label>
              <span>Collection</span>
              <select
                value={smartCollectionId}
                onChange={(event) => setSmartCollectionId(event.target.value)}
              >
                <option value="">Any collection</option>
                {organization.collections.map((collection) => (
                  <option value={collection.collection_id} key={collection.collection_id}>
                    {collection.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Tag</span>
              <select value={smartTagId} onChange={(event) => setSmartTagId(event.target.value)}>
                <option value="">Any tag</option>
                {organization.tags.map((tag) => (
                  <option value={tag.tag_id} key={tag.tag_id}>#{tag.name}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="library-smart-shelf-form-actions">
            <button
              type="submit"
              disabled={!smartShelfName.trim() || !hasSmartShelfDraft || busyKey !== null}
            >
              {editingSmartShelfId ? "Save shelf" : "Create smart shelf"}
            </button>
            {editingSmartShelfId ? (
              <button type="button" onClick={resetSmartShelfForm}>Cancel</button>
            ) : null}
          </div>
        </form>

        <div className="library-manage-list">
          {organization.smart_shelves.map((shelf) => (
            <div key={shelf.smart_shelf_id}>
              <span>{shelf.name} · {shelf.item_count} live</span>
              <div>
                <button type="button" onClick={() => editSmartShelf(shelf)}>Edit</button>
                <button
                  type="button"
                  disabled={busyKey === `smart-shelf:${shelf.smart_shelf_id}`}
                  onClick={() => void onDeleteSmartShelf(shelf.smart_shelf_id)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
          {organization.collections.map((collection) => (
            <div key={collection.collection_id}>
              <span>{collection.name} · {collection.item_count}</span>
              <button
                type="button"
                disabled={busyKey === `collection:${collection.collection_id}`}
                onClick={() => void onDeleteCollection(collection.collection_id)}
              >
                Delete
              </button>
            </div>
          ))}
          {organization.tags.map((tag) => (
            <div key={tag.tag_id}>
              <span>#{tag.name} · {tag.item_count}</span>
              <button
                type="button"
                disabled={busyKey === `tag:${tag.tag_id}`}
                onClick={() => void onDeleteTag(tag.tag_id)}
              >
                Delete
              </button>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}

type OrganizerProps = {
  entryId: string;
  collections: CollectionSummary[];
  tags: TagSummary[];
  organization: LibraryOrganization;
  busyKey: string | null;
  onToggleCollection: (collectionId: string, assigned: boolean) => Promise<void>;
  onAssignTag: (name: string) => Promise<boolean>;
  onRemoveTag: (tagId: string) => Promise<void>;
};

export function LibraryItemOrganizer({
  entryId,
  collections,
  tags,
  organization,
  busyKey,
  onToggleCollection,
  onAssignTag,
  onRemoveTag,
}: OrganizerProps) {
  const [tagName, setTagName] = useState("");
  const assignedCollections = new Set(collections.map((item) => item.collection_id));
  const assignedTags = new Set(tags.map((item) => item.tag_id));

  async function submitTag(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = tagName.trim();
    if (!normalized || busyKey !== null) return;
    if (await onAssignTag(normalized)) setTagName("");
  }

  return (
    <details className="library-card-organizer">
      <summary>Organize</summary>
      <div className="library-card-organizer-body">
        <span className="library-organizer-label">Collections</span>
        {organization.collections.length === 0 ? (
          <p>Create a collection above to build manual shelves.</p>
        ) : (
          <div className="library-collection-checks">
            {organization.collections.map((collection) => {
              const assigned = assignedCollections.has(collection.collection_id);
              const key = `${entryId}:collection:${collection.collection_id}`;
              return (
                <label key={collection.collection_id}>
                  <input
                    type="checkbox"
                    checked={assigned}
                    disabled={busyKey === key}
                    onChange={() => void onToggleCollection(collection.collection_id, assigned)}
                  />
                  <span>{collection.name}</span>
                </label>
              );
            })}
          </div>
        )}

        <span className="library-organizer-label">Tags</span>
        <div className="library-tag-chips">
          {tags.map((tag) => (
            <button
              type="button"
              title={`Remove tag ${tag.name}`}
              disabled={busyKey === `${entryId}:tag:${tag.tag_id}`}
              key={tag.tag_id}
              onClick={() => void onRemoveTag(tag.tag_id)}
            >
              #{tag.name} ×
            </button>
          ))}
        </div>
        <form className="library-tag-form" onSubmit={submitTag}>
          <input
            aria-label="Tag this book"
            maxLength={80}
            placeholder="Add tag"
            value={tagName}
            onChange={(event) => setTagName(event.target.value)}
          />
          <button type="submit" disabled={!tagName.trim() || busyKey !== null}>Tag</button>
        </form>
        {organization.tags.filter((tag) => !assignedTags.has(tag.tag_id)).length > 0 ? (
          <div className="library-existing-tags">
            <span>Existing:</span>
            {organization.tags
              .filter((tag) => !assignedTags.has(tag.tag_id))
              .map((tag) => (
                <button
                  type="button"
                  key={tag.tag_id}
                  onClick={() => void onAssignTag(tag.name)}
                >
                  #{tag.name}
                </button>
              ))}
          </div>
        ) : null}
      </div>
    </details>
  );
}

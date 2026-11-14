"use client";

import { useState } from "react";

import { apiFetch } from "../lib/api";
import styles from "./wordpress-connection-manager.module.css";

type PostPage = {
  plugin_id: string;
  scope: string;
  page: number;
  per_page: number;
  read_only: boolean;
  publishing_authorized: boolean;
  posts: Array<{ id: number; title: string; slug: string; status: string }>;
};

export function WordPressPostBrowser({ enabled }: { enabled: boolean }) {
  const [result, setResult] = useState<PostPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const browse = async (nextPage: number) => {
    if (!enabled || nextPage < 1 || nextPage > 50) return;
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`/v1/plugins/wordpress/posts?page=${nextPage}`);
      if (!response.ok) {
        throw new Error(`WordPress post read unavailable (HTTP ${response.status}).`);
      }
      const data = (await response.json()) as PostPage;
      if (
        data.plugin_id !== "wordpress" ||
        data.scope !== "authenticated_read_only_posts" ||
        !data.read_only ||
        data.publishing_authorized ||
        !Array.isArray(data.posts)
      ) {
        throw new Error("WordPress did not return a safe read-only response.");
      }
      setResult(data);
    } catch (caught) {
      setResult(null);
      setError(caught instanceof Error ? caught.message : "WordPress post read unavailable.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className={styles.panel} aria-label="WordPress post browser">
      <h2>WordPress posts</h2>
      <p className={styles.help}>Browse post summaries, including drafts visible to your account. This feature never modifies or publishes a post.</p>
      {!enabled ? (
        <p>Enable read-only access to browse posts.</p>
      ) : (
        <>
          <button type="button" disabled={loading} onClick={() => void browse(1)}>Browse posts</button>
          {error && <p role="alert" className={styles.error}>{error}</p>}
          {result && (
            <>
              <p role="status">Page {result.page}: {result.posts.length} posts</p>
              {result.posts.length === 0 ? <p>No posts were returned for this page.</p> : (
                <ol>
                  {result.posts.map((post) => (
                    <li key={post.id}>
                      <strong>{post.title || "(Untitled)"}</strong>
                      {" "}({post.status}, #{post.id})
                    </li>
                  ))}
                </ol>
              )}
              {result.posts.length === result.per_page && result.page < 50 && (
                <button
                  type="button"
                  disabled={loading}
                  onClick={() => void browse(result.page + 1)}
                >Next page</button>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

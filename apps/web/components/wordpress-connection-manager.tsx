"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { apiFetch } from "../lib/api";
import styles from "./wordpress-connection-manager.module.css";

type PluginState = {
  plugin_id: string;
  display_name: string;
  version: string;
  installed: boolean;
  enabled: boolean;
  requires_connection: boolean;
  capabilities: string[];
};

type ConnectionState = {
  plugin_id: string;
  mode_id: string;
  configured: boolean;
  settings: Record<string, string | number>;
};

type ReceiptState = "not_configured" | "not_tested" | "verified" | "unavailable";

type ProbeStatus = {
  plugin_id: string;
  state: ReceiptState;
  scope: string;
  checked_at: string | null;
  evidence_code: string | null;
  credentials_verified: boolean;
  publishing_authorized: boolean;
};

type CapabilityStatus = {
  plugin_id: string;
  state: ReceiptState;
  checked_at: string | null;
  evidence_code: string | null;
  capabilities_observed: boolean;
  capabilities: Record<string, boolean>;
  publishing_authorized: boolean;
};

const WORDPRESS_ID = "wordpress";
const ROOT = "/v1/plugins/wordpress";

async function apiError(response: Response): Promise<string> {
  try {
    const data = (await response.json()) as { detail?: string | { code?: string } };
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail === "object" && data.detail.code) {
      return data.detail.code.replaceAll("_", " ").toLowerCase();
    }
  } catch {
    // Do not expose response bodies, headers, or application passwords.
  }
  return `Request failed (HTTP ${response.status}).`;
}

function timestamp(value: string | null): string {
  if (!value) return "Not checked";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown check time" : date.toLocaleString();
}

export function WordPressConnectionManager() {
  const [plugins, setPlugins] = useState<PluginState[]>([]);
  const [connection, setConnection] = useState<ConnectionState | null>(null);
  const [publicStatus, setPublicStatus] = useState<ProbeStatus | null>(null);
  const [identityStatus, setIdentityStatus] = useState<ProbeStatus | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityStatus | null>(null);
  const [siteUrl, setSiteUrl] = useState("");
  const [username, setUsername] = useState("");
  const [applicationPassword, setApplicationPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const wordpress = plugins.find((plugin) => plugin.plugin_id === WORDPRESS_ID);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const catalog = await apiFetch("/v1/plugins");
      if (!catalog.ok) throw new Error(await apiError(catalog));
      const data = (await catalog.json()) as { plugins: PluginState[] };
      setPlugins(data.plugins);
      const current = data.plugins.find((plugin) => plugin.plugin_id === WORDPRESS_ID);
      if (!current?.installed) {
        setConnection(null);
        setPublicStatus(null);
        setIdentityStatus(null);
        setCapabilities(null);
        return;
      }
      const [configured, publicProbe, identityProbe, capabilityProbe] = await Promise.all([
        apiFetch(`${ROOT}/connection`),
        apiFetch(`${ROOT}/connection/public-probe`),
        apiFetch(`${ROOT}/connection/identity-probe`),
        apiFetch(`${ROOT}/connection/capability-probe`),
      ]);
      if (!configured.ok && configured.status !== 404) {
        throw new Error(await apiError(configured));
      }
      if (!publicProbe.ok) throw new Error(await apiError(publicProbe));
      if (!identityProbe.ok) throw new Error(await apiError(identityProbe));
      if (!capabilityProbe.ok) throw new Error(await apiError(capabilityProbe));
      const saved = configured.ok ? ((await configured.json()) as ConnectionState) : null;
      const savedCapabilities = (await capabilityProbe.json()) as CapabilityStatus;
      setConnection(saved);
      setPublicStatus((await publicProbe.json()) as ProbeStatus);
      setIdentityStatus((await identityProbe.json()) as ProbeStatus);
      if (saved) {
        setSiteUrl(String(saved.settings.site_url ?? ""));
        setUsername(String(saved.settings.username ?? ""));
      } else {
        setSiteUrl("");
        setUsername("");
      }
      setCapabilities(
        savedCapabilities.state === "not_tested" || savedCapabilities.state === "not_configured"
          ? null
          : savedCapabilities,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Connection state unavailable.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (
    operation: () => Promise<Response>,
    success: string,
    reload = true,
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const response = await operation();
      if (!response.ok) throw new Error(await apiError(response));
      setNotice(success);
      if (reload) await refresh();
      return response;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Operation unavailable.");
      return null;
    } finally {
      setBusy(false);
    }
  };

  const install = async () => {
    await act(
      () => apiFetch(`${ROOT}/install`, { method: "POST" }),
      "WordPress plugin installed. Configure a REST connection to proceed.",
    );
  };

  const configure = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const secret = applicationPassword;
    setApplicationPassword("");
    if (!secret.trim()) {
      setError("Enter an Application Password to save or rotate your connection.");
      return;
    }
    const response = await act(
      () =>
        apiFetch(`${ROOT}/connection`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            mode_id: "rest",
            values: {
              site_url: siteUrl.trim(),
              username: username.trim(),
              application_password: secret,
            },
          }),
        }),
      "Connection saved. Previous verification evidence has been invalidated.",
    );
    if (response) setCapabilities(null);
  };

  const setEnabled = async (enabled: boolean) => {
    await act(
      () =>
        apiFetch(ROOT, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        }),
      enabled
        ? "WordPress enabled for guarded plugin actions. Publishing is still disabled."
        : "WordPress disabled. Saved connection settings remain available.",
    );
  };

  const probe = async (kind: "public" | "identity" | "capability") => {
    const route =
      kind === "public"
        ? "public-probe"
        : kind === "identity"
          ? "identity-probe"
          : "capability-probe";
    const response = await act(
      () => apiFetch(`${ROOT}/connection/${route}`, { method: "POST" }),
      "Verification completed. Review the observed evidence below.",
      false,
    );
    if (!response) return;
    const result = await response.json();
    if (kind === "public") {
      setPublicStatus(result as ProbeStatus);
    } else if (kind === "identity") {
      setIdentityStatus(result as ProbeStatus);
    } else {
      setCapabilities(result as CapabilityStatus);
    }
  };

  const uninstall = async () => {
    if (!window.confirm(
      "Uninstall WordPress? This destroys its saved connection and credentials. Your library remains intact.",
    )) return;
    const response = await act(
      () => apiFetch(ROOT, { method: "DELETE" }),
      "WordPress plugin uninstalled. Its saved credentials have been revoked.",
    );
    if (response) {
      setApplicationPassword("");
      setSiteUrl("");
      setUsername("");
    }
  };

  return (
    <section className={styles.manager} aria-label="WordPress plugin management">
      <div className={styles.intro}>
        <h1>WordPress connection</h1>
        <p>Manage a WordPress REST connection and inspect its evidence. Publishing and direct database access are not enabled.</p>
      </div>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}
      {loading ? <p role="status">Loading plugin state…</p> : (
        <>
          <section className={styles.panel} aria-label="Plugin installation">
            <div className={styles.heading}>
              <div>
                <h2>{wordpress?.display_name ?? "WordPress"}</h2>
                <p>
                  Version {wordpress?.version ?? "unavailable"} · {wordpress?.installed ? "Installed" : "Not installed"}
                  {wordpress?.installed ? ` · ${wordpress.enabled ? "Enabled" : "Disabled"}` : ""}
                </p>
              </div>
              {!wordpress?.installed && <button type="button" disabled={busy || !wordpress} onClick={() => void install()}>Install</button>}
              {wordpress?.installed && connection && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void setEnabled(!wordpress.enabled)}
                >
                  {wordpress.enabled ? "Disable WordPress" : "Enable WordPress"}
                </button>
              )}
            </div>
            {wordpress?.installed && (
              <p className={styles.help}>
                {connection
                  ? "Activation is explicit. Enabled plugins may enter guarded product actions, but capability observations still do not authorize publishing."
                  : "Save a complete connection before enabling this plugin. Configuration never activates it automatically."}
              </p>
            )}
          </section>

          {wordpress?.installed && (
            <>
              <section className={styles.panel} aria-label="REST connection settings">
                <h2>REST connection settings</h2>
                <p className={styles.help}>Use your WordPress HTTPS site URL, account username, and an Application Password. The password is write-only and is never displayed after saving.</p>
                <form onSubmit={(event) => void configure(event)} className={styles.form}>
                  <label>Site URL
                    <input type="url" autoComplete="url" required placeholder="https://your-site.example" value={siteUrl} onChange={(event) => setSiteUrl(event.target.value)} disabled={busy} />
                  </label>
                  <label>WordPress username
                    <input type="text" autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} disabled={busy} />
                  </label>
                  <label>Application Password
                    <input type="password" autoComplete="new-password" required value={applicationPassword} onChange={(event) => setApplicationPassword(event.target.value)} disabled={busy} />
                  </label>
                  <button disabled={busy} type="submit">{connection ? "Save and rotate connection" : "Save connection"}</button>
                </form>
              </section>

              <section className={styles.panel} aria-label="Connection verification">
                <h2>Connection verification</h2>
                {!connection ? <p>Save a connection before testing.</p> : (
                  <div className={styles.checks}>
                    <article>
                      <h3>Public REST reachability</h3>
                      <p>{publicStatus?.state ?? "not_tested"} · {timestamp(publicStatus?.checked_at ?? null)}</p>
                      <p className={styles.help}>{publicStatus?.evidence_code ?? "No observation"}</p>
                      <button type="button" disabled={busy} onClick={() => void probe("public")}>Test public REST</button>
                    </article>
                    <article>
                      <h3>Application Password identity</h3>
                      <p>{identityStatus?.state ?? "not_tested"} · {timestamp(identityStatus?.checked_at ?? null)}</p>
                      <p className={styles.help}>{identityStatus?.evidence_code ?? "No observation"}</p>
                      <button type="button" disabled={busy} onClick={() => void probe("identity")}>Verify credentials</button>
                    </article>
                    <article>
                      <h3>WordPress capabilities</h3>
                      <p>Last observed capability evidence is restored after reload. It remains evidence only and does not establish publishing permission.</p>
                      <button type="button" disabled={busy} onClick={() => void probe("capability")}>Inspect capabilities</button>
                      {capabilities && (
                        <div className={styles.result}>
                          <p>{capabilities.state} · {timestamp(capabilities.checked_at)}</p>
                          <p className={styles.help}>{capabilities.evidence_code}</p>
                          <dl>
                            {Object.entries(capabilities.capabilities).map(([name, allowed]) => (
                              <div key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{allowed ? "Reported: yes" : "Not reported"}</dd></div>
                            ))}
                          </dl>
                        </div>
                      )}
                    </article>
                  </div>
                )}
                <p className={styles.help}>All checks are read-only. No test creates, modifies, or publishes a post. Requests may be rate-limited for 60 seconds per check.</p>
              </section>

              <section className={styles.panel} aria-label="Uninstall WordPress">
                <h2>Uninstall</h2>
                <p className={styles.help}>Removes this plugin installation, its configuration, and credentials. Canonical library data is preserved.</p>
                <button type="button" className={styles.danger} onClick={() => void uninstall()} disabled={busy}>Uninstall WordPress</button>
              </section>
            </>
          )}
        </>
      )}
    </section>
  );
}

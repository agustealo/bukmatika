const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

let sessionPromise: Promise<void> | null = null;

export async function ensureLocalSession(): Promise<void> {
  if (sessionPromise === null) {
    sessionPromise = fetch(`${API_BASE}/v1/session/local`, {
      method: "POST",
      credentials: "include",
    }).then((response) => {
      if (!response.ok) {
        throw new Error(`Session bootstrap failed with HTTP ${response.status}.`);
      }
    });
    sessionPromise.catch(() => {
      sessionPromise = null;
    });
  }
  await sessionPromise;
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  await ensureLocalSession();
  return fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
  });
}

export { API_BASE };

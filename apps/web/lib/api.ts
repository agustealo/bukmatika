const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const API_MUTATION_EVENT = "bukmatika:api-mutation";

export type ApiMutationDetail = {
  path: string;
  method: string;
};

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
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
  });

  const method = (init.method ?? "GET").toUpperCase();
  if (
    response.ok &&
    method !== "GET" &&
    method !== "HEAD" &&
    typeof window !== "undefined"
  ) {
    window.dispatchEvent(
      new CustomEvent<ApiMutationDetail>(API_MUTATION_EVENT, {
        detail: { path, method },
      }),
    );
  }

  return response;
}

export { API_BASE };

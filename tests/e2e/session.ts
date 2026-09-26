import { expect, type BrowserContext } from "@playwright/test";

export type SessionResponse = {
  principal_id: string;
  session_id: string;
  expires_at: string;
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export async function bootstrapLocalSession(context: BrowserContext): Promise<SessionResponse> {
  const response = await context.request.post("http://127.0.0.1:8000/v1/session/local");
  expect(
    response.ok(),
    `Local session bootstrap failed with HTTP ${response.status()}.`,
  ).toBeTruthy();

  const session = (await response.json()) as SessionResponse;
  expect(session.principal_id).toMatch(UUID_PATTERN);
  expect(session.session_id).toMatch(UUID_PATTERN);
  expect(Number.isNaN(Date.parse(session.expires_at))).toBeFalsy();
  return session;
}

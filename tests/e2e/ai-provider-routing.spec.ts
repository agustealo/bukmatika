import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

test("cloud-selected AI route renders and clears roles without probing local Ollama inventory", async ({ page, context }) => {
  await bootstrapLocalSession(context);

  let localInventoryRequests = 0;
  let roleClearRequests = 0;
  let assignments = [
    {
      assignment_id: "22222222-2222-2222-2222-222222222222",
      connection_id: "11111111-1111-1111-1111-111111111111",
      role: "research",
      model_id: "reasoner-v1",
      priority: 0,
      enabled: true,
      capabilities: ["structured_generation", "text_generation"],
    },
  ];

  await page.route("**/v1/ai/status", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        configured: true,
        ready: true,
        ai_enabled: true,
        state: "ready",
        provider: "synthetic-cloud",
        model: "reasoner-v1",
        routing: "cloud",
      }),
    });
  });

  await page.route("**/v1/ai/routing-policy", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ai_enabled: true,
        model_selection_mode: "profile",
        cloud_egress_policy: "public_only",
      }),
    });
  });

  await page.route("**/v1/ai/providers", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        providers: [
          {
            provider_id: "synthetic-cloud",
            display_name: "Synthetic Cloud",
            routing_type: "cloud",
          },
        ],
      }),
    });
  });

  await page.route("**/v1/ai/provider-connections", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        connections: [
          {
            connection_id: "11111111-1111-1111-1111-111111111111",
            provider_id: "synthetic-cloud",
            display_name: "Research cloud",
            routing_type: "cloud",
            status: "enabled",
            credential_configured: true,
            created_at: "2026-09-29T00:00:00Z",
            updated_at: "2026-09-29T00:00:00Z",
          },
        ],
      }),
    });
  });

  await page.route("**/v1/ai/model-assignments/research", async (route) => {
    if (route.request().method() === "DELETE") {
      roleClearRequests += 1;
      assignments = [];
      await route.fulfill({ status: 204 });
      return;
    }
    await route.fallback();
  });

  await page.route("**/v1/ai/model-assignments", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ assignments }),
    });
  });

  await page.route("**/v1/ai/local/models", async (route) => {
    localInventoryRequests += 1;
    await route.abort("failed");
  });

  await page.goto("/personalization");

  await expect(page.getByRole("heading", { name: "Providers, models & privacy" })).toBeVisible();
  await expect(page.getByText("synthetic-cloud", { exact: true })).toBeVisible();
  await expect(page.getByText("reasoner-v1", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByRole("paragraph").filter({ hasText: /^Research cloud · cloud$/ }),
  ).toBeVisible();
  await expect(page.getByText("Credential configured", { exact: true })).toBeVisible();
  await expect(page.getByText("Cloud: public/evidence-only data", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Load installed Ollama models" })).toHaveCount(0);

  await page.getByRole("button", { name: "Clear Research role" }).click();
  await expect(page.getByText("No active profile role assignments.", { exact: true })).toBeVisible();

  expect(roleClearRequests).toBe(1);
  expect(localInventoryRequests).toBe(0);
});

import { expect, test } from "@playwright/test";

import { bootstrapLocalSession } from "./session";

test("cloud-selected AI route never probes local Ollama inventory", async ({ page, context }) => {
  await bootstrapLocalSession(context);

  let localInventoryRequests = 0;

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

  await page.route("**/v1/ai/configuration", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        mode: "ollama",
        source: "profile",
        selected_model: "reasoner-v1",
        effective_provider: "synthetic-cloud",
        effective_model: "reasoner-v1",
        installation_provider: "ollama",
        installation_model: "llama3.2:latest",
        routing: "local",
      }),
    });
  });

  await page.route("**/v1/ai/local/models", async (route) => {
    localInventoryRequests += 1;
    await route.abort("failed");
  });

  await page.goto("/personalization");

  await expect(page.getByRole("heading", { name: "Model setup & readiness" })).toBeVisible();
  await expect(page.getByText("synthetic-cloud", { exact: true })).toBeVisible();
  await expect(page.getByText("reasoner-v1", { exact: true })).toBeVisible();
  await expect(page.getByText("cloud", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "This profile is routed through a cloud provider." }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "Use selected model" })).toHaveCount(0);

  expect(localInventoryRequests).toBe(0);
});

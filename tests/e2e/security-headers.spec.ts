import { expect, test } from "@playwright/test";

test("production web responses publish the browser security contract", async ({ request }) => {
  const response = await request.get("/research");

  expect(response.status()).toBe(200);
  const headers = response.headers();

  expect(headers["content-security-policy"]).toBe(
    "base-uri 'self'; frame-ancestors 'none'; object-src 'none'",
  );
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["permissions-policy"]).toBe(
    "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  );
  expect(headers["x-powered-by"]).toBeUndefined();
});

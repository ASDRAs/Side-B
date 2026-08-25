const { expect, test } = require("@playwright/test");

const {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
} = require("./extension.cjs");

const accessToken = process.env.SIDE_B_E2E_ACCESS_TOKEN?.trim() || "";
const configuredApiBaseUrl = process.env.SIDE_B_API_BASE_URL?.replace(/\/+$/, "");
const query = process.env.SIDE_B_E2E_QUERY || "Radiohead - Creep";

if (!accessToken) {
  throw new Error(
    "SIDE_B_E2E_ACCESS_TOKEN is required for the deployed backend smoke test.",
  );
}

test("side panel requests recommendations from the deployed backend", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    const apiBaseUrlInput = page.locator("#apiBaseUrl");
    if (configuredApiBaseUrl) {
      await apiBaseUrlInput.fill(configuredApiBaseUrl);
    } else {
      await expect(apiBaseUrlInput).not.toHaveValue("");
    }
    const apiBaseUrl = (await apiBaseUrlInput.inputValue()).replace(/\/+$/, "");
    assertApiOriginIsAllowed(apiBaseUrl);
    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);

    const responsePromise = page.waitForResponse(
      (response) =>
        response.url() === `${apiBaseUrl}/recommend` &&
        response.request().method() === "POST",
      { timeout: 95_000 },
    );

    await page.locator("#submitButton").click();
    const response = await responsePromise;
    const requestPayload = response.request().postDataJSON();
    const responsePayload = await response.json();

    expect(response.status()).toBe(200);
    expect(response.request().headers()["x-side-b-access-token"]).toBe(
      accessToken,
    );
    expect(requestPayload).toEqual({ query, top_n: 10 });
    expect(responsePayload.top_n).toBe(10);

    const buckets = Object.entries(responsePayload.result || {}).filter(
      ([, tracks]) => Array.isArray(tracks),
    );
    expect(buckets).toHaveLength(3);
    const totalTracks = buckets.reduce(
      (sum, [, tracks]) => sum + tracks.length,
      0,
    );
    expect(totalTracks).toBeGreaterThan(0);
    expect(totalTracks).toBeLessThanOrEqual(30);

    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    await expect(page.locator("#statusMessage")).toContainText(
      `추천 결과 ${totalTracks}곡`,
    );

    await testInfo.attach("recommend-response.json", {
      body: Buffer.from(JSON.stringify(responsePayload, null, 2)),
      contentType: "application/json",
    });
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

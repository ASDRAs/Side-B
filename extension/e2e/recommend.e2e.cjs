const { expect, test } = require("@playwright/test");

const {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
} = require("./extension.cjs");

const apiBaseUrl = (
  process.env.SIDE_B_API_BASE_URL ||
  "https://side-b-backend-7hmhv6htsa-du.a.run.app"
).replace(/\/+$/, "");
const query = process.env.SIDE_B_E2E_QUERY || "Radiohead - Creep";

test("popup requests recommendations from the deployed backend", async ({}, testInfo) => {
  assertApiOriginIsAllowed(apiBaseUrl);
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    const apiBaseUrlInput = page.locator("#apiBaseUrl");
    if (process.env.SIDE_B_API_BASE_URL) {
      await apiBaseUrlInput.fill(apiBaseUrl);
    } else {
      await expect(apiBaseUrlInput).toHaveValue(apiBaseUrl);
    }
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
    expect(requestPayload).toEqual({ query, top_n: 10 });
    expect(responsePayload.top_n).toBe(10);

    const buckets = Object.entries(responsePayload.result || {}).filter(
      ([, tracks]) => Array.isArray(tracks),
    );
    expect(buckets).toHaveLength(3);
    expect(buckets.map(([bucketName]) => bucketName)).toContain("similar");
    expect(buckets.map(([bucketName]) => bucketName)).toContain("hidden");
    expect(
      buckets.some(([bucketName]) =>
        ["reverse", "opposite"].includes(bucketName),
      ),
    ).toBe(true);

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

    for (const [bucketName, tracks] of buckets) {
      expect(tracks.length).toBeLessThanOrEqual(10);
      if (tracks.length === 0) {
        continue;
      }
      const bucket = page.locator(`.bucket[data-bucket="${bucketName}"]`);
      await expect(bucket).toBeVisible();
      await expect(bucket.locator(".track-item")).toHaveCount(tracks.length);
    }

    const renderedPayload = JSON.parse(
      (await page.locator("#rawResponse").textContent()) || "null",
    );
    expect(renderedPayload).toEqual(responsePayload);

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

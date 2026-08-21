const fs = require("node:fs");
const path = require("node:path");

const { chromium, expect, test } = require("@playwright/test");

const extensionPath = path.resolve(__dirname, "..");
const manifestPath = path.join(extensionPath, "manifest.json");
const apiBaseUrl = (
  process.env.SIDE_B_API_BASE_URL ||
  "https://side-b-backend-7hmhv6htsa-du.a.run.app"
).replace(/\/+$/, "");
const query = process.env.SIDE_B_E2E_QUERY || "Radiohead - Creep";

function assertApiOriginIsAllowed() {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const requiredPermission = `${new URL(apiBaseUrl).origin}/*`;
  if (!manifest.host_permissions?.includes(requiredPermission)) {
    throw new Error(
      `manifest.json host_permissions에 ${requiredPermission}를 추가하세요.`,
    );
  }
}

test("popup requests recommendations from the deployed backend", async ({}, testInfo) => {
  assertApiOriginIsAllowed();

  const context = await chromium.launchPersistentContext("", {
    channel: "chromium",
    headless: testInfo.project.use.headless !== false,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
    ],
  });

  let page;
  try {
    let [serviceWorker] = context.serviceWorkers();
    if (!serviceWorker) {
      serviceWorker = await context.waitForEvent("serviceworker");
    }
    const extensionId = new URL(serviceWorker.url()).host;

    page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/popup.html`);
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
    );

    await page.locator("#submitButton").click();
    const response = await responsePromise;
    const requestPayload = response.request().postDataJSON();
    const responsePayload = await response.json();

    expect(response.status()).toBe(200);
    expect(requestPayload).toEqual({ query, top_n: 10 });
    expect(responsePayload.top_n).toBe(10);

    const populatedBuckets = Object.entries(responsePayload.result || {}).filter(
      ([, tracks]) => Array.isArray(tracks) && tracks.length > 0,
    );
    expect(populatedBuckets).toHaveLength(3);

    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    await expect(page.locator("#statusMessage")).toContainText("추천 결과 30곡");

    for (const [bucketName, tracks] of populatedBuckets) {
      expect(tracks).toHaveLength(10);
      const bucket = page.locator(`.bucket[data-bucket="${bucketName}"]`);
      await expect(bucket).toBeVisible();
      await expect(bucket.locator(".track-item")).toHaveCount(10);
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
    if (page) {
      await page.screenshot({
        path: testInfo.outputPath("popup-failure.png"),
        fullPage: true,
      });
    }
    throw error;
  } finally {
    await context.close();
  }
});

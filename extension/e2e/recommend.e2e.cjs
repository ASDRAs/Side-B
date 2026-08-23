const { expect, test } = require("@playwright/test");

const {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
} = require("./extension.cjs");

const accessToken = "side-b-e2e-token";
const query = "Radiohead - Creep";
const recommendationPayload = {
  track_name: "Creep",
  artist: "Radiohead",
  top_n: 10,
  result: {
    similar: [{ name: "Karma Police", artist: "Radiohead" }],
    reverse: [],
    hidden: [{ name: "Lucky", artist: "Radiohead" }],
  },
};

test("popup sends an authenticated recommendation and renders it", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let capturedRequest = null;

  try {
    const apiBaseUrlInput = page.locator("#apiBaseUrl");
    await expect(apiBaseUrlInput).not.toHaveValue("");
    const apiBaseUrl = (await apiBaseUrlInput.inputValue()).replace(/\/+$/, "");
    assertApiOriginIsAllowed(apiBaseUrl);

    await page.route(`${apiBaseUrl}/recommend`, async (route) => {
      capturedRequest = {
        payload: route.request().postDataJSON(),
        token: route.request().headers()["x-side-b-access-token"],
      };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recommendationPayload),
      });
    });

    await page.locator("#youtubeExportToken").fill(accessToken);
    await page.locator("#query").fill(query);
    const responsePromise = page.waitForResponse(`${apiBaseUrl}/recommend`);
    await page.locator("#submitButton").click();
    await responsePromise;

    expect(capturedRequest).toEqual({
      payload: { query, top_n: 10 },
      token: accessToken,
    });
    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    await expect(page.locator("#statusMessage")).toContainText("추천 결과 2곡");
    await expect(page.locator(".track-item")).toHaveCount(2);

    const renderedPayload = JSON.parse(
      (await page.locator("#rawResponse").textContent()) || "null",
    );
    expect(renderedPayload).toEqual(recommendationPayload);
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

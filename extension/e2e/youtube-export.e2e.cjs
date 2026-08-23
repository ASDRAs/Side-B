const { expect, test } = require("@playwright/test");

const {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
} = require("./extension.cjs");

const configuredApiBaseUrl = process.env.SIDE_B_API_BASE_URL?.replace(/\/+$/, "");
const liveExportToken =
  process.env.SIDE_B_E2E_ACCESS_TOKEN?.trim() ||
  process.env.SIDE_B_E2E_EXPORT_TOKEN?.trim() ||
  "";
const exportToken = liveExportToken || "side-b-e2e-token";
const seedTrack = { name: "Blinding Lights", artist: "The Weeknd" };
const recommendationPayload = {
  track_name: seedTrack.name,
  artist: seedTrack.artist,
  top_n: 10,
  result: {
    similar: [seedTrack],
    reverse: [],
    hidden: [],
  },
};
const mockedMatchesPayload = {
  bucket: "similar",
  requested: 1,
  matched: [
    {
      ...seedTrack,
      video_id: "4NRXx6U8ABQ",
      youtube_title: "The Weeknd - Blinding Lights (Official Video)",
      channel_title: "The Weeknd",
      confidence: 0.99,
      position: 0,
    },
  ],
  unmatched: [],
  deduplicated: 0,
};

test("popup reaches YouTube match review before OAuth", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let capturedMatchRequest = null;
  let capturedRecommendRequest = null;

  try {
    const apiBaseUrlInput = page.locator("#apiBaseUrl");
    if (configuredApiBaseUrl) {
      await apiBaseUrlInput.fill(configuredApiBaseUrl);
    } else {
      await expect(apiBaseUrlInput).not.toHaveValue("");
    }
    const apiBaseUrl = (await apiBaseUrlInput.inputValue()).replace(/\/+$/, "");
    assertApiOriginIsAllowed(apiBaseUrl);

    await page.route(`${apiBaseUrl}/recommend`, (route) => {
      capturedRecommendRequest = {
        token: route.request().headers()["x-side-b-access-token"],
      };
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recommendationPayload),
      });
    });

    if (!liveExportToken) {
      await page.route(`${apiBaseUrl}/exports/youtube/matches`, async (route) => {
        capturedMatchRequest = {
          payload: route.request().postDataJSON(),
          token: route.request().headers()["x-side-b-export-token"],
        };
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(mockedMatchesPayload),
        });
      });
    }

    await page.locator("#youtubeExportToken").fill(exportToken);
    await page.locator("#query").fill(`${seedTrack.artist} - ${seedTrack.name}`);

    const recommendResponsePromise = page.waitForResponse(
      `${apiBaseUrl}/recommend`,
    );
    await page.locator("#submitButton").click();
    await recommendResponsePromise;
    expect(capturedRecommendRequest).toEqual({ token: exportToken });

    const matchResponsePromise = page.waitForResponse(
      (response) =>
        response.url() === `${apiBaseUrl}/exports/youtube/matches` &&
        response.request().method() === "POST",
      { timeout: 95_000 },
    );
    await page
      .locator('.bucket[data-bucket="similar"] .export-button')
      .click();

    const matchResponse = await matchResponsePromise;
    const matchRequest = matchResponse.request().postDataJSON();
    const matchesPayload = await matchResponse.json();

    expect(matchResponse.status()).toBe(200);
    expect(matchRequest).toEqual({ bucket: "similar", tracks: [seedTrack] });
    expect(matchesPayload.matched?.length).toBeGreaterThan(0);
    if (capturedMatchRequest) {
      expect(capturedMatchRequest).toEqual({
        payload: { bucket: "similar", tracks: [seedTrack] },
        token: exportToken,
      });
    }

    await expect(page.locator("#youtubeExportStatus")).toHaveText("매칭 확인");
    await expect(page.locator("#youtubeMatchReview")).toBeVisible();
    await expect(page.locator("#youtubeMatchList .match-item")).toHaveCount(
      (matchesPayload.matched?.length || 0) +
        (matchesPayload.unmatched?.length || 0),
    );

    await testInfo.attach("youtube-matches-response.json", {
      body: Buffer.from(JSON.stringify(matchesPayload, null, 2)),
      contentType: "application/json",
    });

    await page.locator("#youtubeMatchCancel").click();
    await expect(page.locator("#youtubeExportStatus")).toHaveText("취소됨");
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

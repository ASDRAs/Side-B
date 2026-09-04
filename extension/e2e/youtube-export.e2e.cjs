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

test("side panel reaches YouTube match review before OAuth", async ({}, testInfo) => {
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

    await page.locator("#backendAccessToken").fill(exportToken);
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

    // native dialog가 배경을 inert로 만든다. Tab을 돌려도 뒤쪽 컨트롤에는
    // 절대 닿지 않는다. 마지막 요소에서 브라우저 UI로 넘어가는 구간이 있으므로
    // "항상 dialog 안"이 아니라 "배경에는 결코 가지 않음"을 확인한다.
    const backgroundControls = [
      "query",
      "submitButton",
      "currentTrackButton",
      "settingsToggle",
      "backendAccessToken",
      "eqTestButton",
      "eqStopButton",
    ];
    for (let press = 0; press < 12; press += 1) {
      await page.keyboard.press("Tab");
      const active = await page.evaluate(
        () => document.activeElement?.id || "",
      );
      expect(backgroundControls).not.toContain(active);
    }

    await page.locator("#youtubeMatchCancel").click();
    await expect(page.locator("#youtubeExportStatus")).toHaveText("취소됨");
    // 닫으면 내보내기를 시작한 버튼으로 포커스가 돌아온다.
    await expect(
      page.locator('.bucket[data-bucket="similar"] .export-button'),
    ).toBeFocused();

    // 확정 경로는 플레이리스트 생성이 이어져 내보내기 버튼이 비활성인 채로
    // dialog가 닫힌다. OAuth까지 가지 않도록 배경 메시지만 가로챈다.
    await page.evaluate(() => {
      const original = chrome.runtime.sendMessage.bind(chrome.runtime);
      chrome.runtime.sendMessage = async (message) => {
        if (message?.type !== "CREATE_YOUTUBE_PLAYLIST") {
          return original(message);
        }
        const { payload } = message;
        // 실제 생성은 OAuth와 여러 API 호출을 거친다. 즉시 응답하면 버튼이
        // 곧바로 다시 활성화돼 포커스 결함이 가려진다.
        await new Promise((resolve) => setTimeout(resolve, 400));
        return {
          ok: true,
          state: {
            status: "completed",
            operationId: payload.operation_id,
            title: payload.title,
            requested: payload.requested,
            matched: payload.matched,
            toAdd: payload.items.length,
            added: payload.items.length,
            failed: [],
            youtubeUrl: "https://www.youtube.com/playlist?list=stub",
            youtubeMusicUrl: "https://music.youtube.com/playlist?list=stub",
          },
        };
      };
    });

    await page
      .locator('.bucket[data-bucket="similar"] .export-button')
      .click();
    await expect(page.locator("#youtubeMatchReview")).toBeVisible();
    await page.locator("#youtubeMatchConfirm").click();
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    // 이 시점에는 생성이 아직 진행 중이라 내보내기 버튼이 비활성이다.
    // 이 상태가 바로 포커스를 잃던 조건이다.
    await expect(
      page.locator('.bucket[data-bucket="similar"] .export-button'),
    ).toBeDisabled();

    // 확정 뒤 포커스가 <body>로 떨어지면 키보드 사용자는 위치를 잃는다.
    // 비활성 버튼은 포커스를 못 받으므로 선택된 탭이 대신 받아야 한다.
    const focusedAfterConfirm = await page.evaluate(() => {
      const active = document.activeElement;
      if (!active || active === document.body) {
        return "body";
      }
      if (active.classList.contains("bucket-tab")) {
        return "bucket-tab";
      }
      if (active.classList.contains("export-button")) {
        return "export-button";
      }
      return active.tagName;
    });
    expect(["bucket-tab", "export-button"]).toContain(focusedAfterConfirm);
    await expect(page.locator("#youtubeExportStatus")).toHaveText("완료");
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

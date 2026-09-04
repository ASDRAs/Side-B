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

test("side panel sends an authenticated recommendation and renders it", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let capturedRequest = null;

  try {
    const apiBaseUrlInput = page.locator("#apiBaseUrl");
    await expect(apiBaseUrlInput).not.toHaveValue("");
    const apiBaseUrl = (await apiBaseUrlInput.inputValue()).replace(/\/+$/, "");
    assertApiOriginIsAllowed(apiBaseUrl);

    // 개별 곡 열기가 백엔드나 Google API를 건드리지 않는지 세기 위해 기록한다.
    const apiRequests = [];
    page.on("request", (request) => {
      const url = request.url();
      if (url.startsWith(apiBaseUrl) || url.includes("googleapis.com")) {
        apiRequests.push(url);
      }
    });
    // 새 탭이 실제 YouTube Music으로 나가지 않도록 막는다.
    await context.route("https://music.youtube.com/**", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<html></html>" }),
    );

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

    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);
    const responsePromise = page.waitForResponse(`${apiBaseUrl}/recommend`);
    await page.locator("#submitButton").click();
    await responsePromise;

    expect(capturedRequest).toEqual({
      payload: { query, top_n: 10 },
      token: accessToken,
    });
    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    // 합계는 응답 전체에서 세고, 목록은 선택한 한 방향만 그린다.
    await expect(page.locator("#statusMessage")).toContainText("추천 결과 2곡");
    await expect(page.locator(".track-item")).toHaveCount(1);

    // direct 응답에 opposite 키가 없으므로 탭도 셋이다. 0곡인 reverse는 남는다.
    await expect(page.locator(".bucket-tab")).toHaveCount(3);
    await expect(
      page.locator('.bucket-tab[aria-selected="true"]'),
    ).toContainText("유사한 곡");
    await expect(page.locator(".track-title")).toHaveText("Karma Police");

    // 선택되지 않은 탭의 aria-controls도 실재하는 패널을 가리켜야 한다.
    // 패널을 하나만 남기고 갈아 끼우므로 대상은 고정된 #results 하나다.
    expect(
      await page.evaluate(() =>
        [...document.querySelectorAll(".bucket-tab")].map((tab) =>
          Boolean(document.getElementById(tab.getAttribute("aria-controls"))),
        ),
      ),
    ).toEqual([true, true, true]);
    expect(
      await page.evaluate(() =>
        document
          .querySelector("#results")
          .getAttribute("aria-labelledby"),
      ),
    ).toBe("bucketTab-similar");
    await expect(page.locator(".track-open")).toHaveAttribute(
      "href",
      "https://music.youtube.com/search?q=Radiohead%20Karma%20Police",
    );

    const requestsBeforeOpen = apiRequests.length;
    const [openedTab] = await Promise.all([
      page.waitForEvent("popup"),
      page.locator(".track-open").click(),
    ]);
    expect(openedTab.url()).toBe(
      "https://music.youtube.com/search?q=Radiohead%20Karma%20Police",
    );
    await openedTab.close();
    // 곡 열기는 검색 URL만 만든다. quota도 OAuth도 쓰지 않는다.
    expect(apiRequests.length).toBe(requestsBeforeOpen);

    // 실행됐지만 0곡인 방향은 사라지지 않고 빈 상태를 설명한다.
    await page.locator(".bucket-tab").nth(1).click();
    await expect(
      page.locator('.bucket[data-bucket="reverse"] .bucket-empty'),
    ).toBeVisible();
    await expect(page.locator(".track-item")).toHaveCount(0);
    await expect(page.locator(".export-button")).toHaveCount(0);

    // 방향키로도 탭을 옮길 수 있어야 한다.
    await page.locator('.bucket-tab[aria-selected="true"]').press("ArrowRight");
    await expect(
      page.locator('.bucket-tab[aria-selected="true"]'),
    ).toContainText("숨겨진 곡");
    await expect(page.locator(".track-title")).toHaveText("Lucky");
    expect(apiRequests.length).toBe(requestsBeforeOpen);

    // 좁은 사이드패널에서도 가로 스크롤이 생기면 안 된다.
    for (const width of [280, 360, 480]) {
      await page.setViewportSize({ width, height: 800 });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
    }
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

test("추천 요청을 취소하고 곧바로 다시 요청할 수 있다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);

  try {
    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
      /\/+$/,
      "",
    );
    assertApiOriginIsAllowed(apiBaseUrl);

    let releaseFirstRequest;
    let requestCount = 0;
    await page.route(`${apiBaseUrl}/recommend`, async (route) => {
      requestCount += 1;
      if (requestCount === 1) {
        // 사용자가 취소를 누를 때까지 첫 요청을 붙잡아 둔다.
        await new Promise((resolve) => {
          releaseFirstRequest = resolve;
        });
      }
      await route
        .fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(recommendationPayload),
        })
        .catch(() => {
          // 취소된 요청은 이미 사라졌다. fulfill 실패는 정상이다.
        });
    });

    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);
    await page.locator("#submitButton").click();
    await expect(page.locator("#submitButton")).toHaveText("취소");

    // 요청 중 검색어를 지워도 브라우저의 required 검증보다 취소가 우선한다.
    await page.locator("#query").fill("");
    await page.locator("#submitButton").click();
    await expect(page.locator("#statusMessage")).toHaveText(
      "추천 요청을 취소했습니다.",
    );
    await expect(page.locator("#submitButton")).toHaveText("추천 요청");
    // 취소는 서버 상태에 대한 판단이 아니므로 연결 배지를 실패로 바꾸지 않는다.
    await expect(page.locator("#connectionBadge")).not.toHaveText("연결 실패");

    releaseFirstRequest?.();

    await page.locator("#query").fill(query);
    const responsePromise = page.waitForResponse(`${apiBaseUrl}/recommend`);
    await page.locator("#submitButton").click();
    await responsePromise;

    // 취소한 요청이 다음 요청을 막지 않는다.
    await expect(page.locator("#statusMessage")).toContainText("추천 결과 2곡");
    await expect(page.locator(".track-item")).toHaveCount(1);
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

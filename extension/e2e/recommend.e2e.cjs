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

for (const { phase, readFails } of [
  { phase: "pending", readFails: false },
  { phase: "completed", readFails: false },
  { phase: "cancelled", readFails: false },
  { phase: "pending", readFails: true },
]) {
  test(`late current-track read cannot replace a newer manual search (${phase}, failure=${readFails})`, async ({}, testInfo) => {
    const { context, page } = await launchExtensionPage(testInfo);
    const requests = [];
    let releaseResponse;
    let signalRequest;
    const responseGate = new Promise((resolve) => { releaseResponse = resolve; });
    const requestArrived = new Promise((resolve) => { signalRequest = resolve; });

    try {
      const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
      await page.route(`${apiBaseUrl}/recommend`, async (route) => {
        requests.push(route.request().postDataJSON().query);
        signalRequest();
        await responseGate;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(recommendationPayload),
        }).catch(() => {});
      });
      await page.locator("#backendAccessToken").fill(accessToken);
      await page.evaluate(() => {
        const send = chrome.runtime.sendMessage.bind(chrome.runtime);
        chrome.runtime.sendMessage = (message, ...args) =>
          message.type === "GET_MUSIC_TAB"
            ? Promise.resolve({ ok: true, tabId: 123 })
            : send(message, ...args);
        chrome.scripting.executeScript = () => new Promise((resolve, reject) => {
          window.finishTrackRead = (fails) => fails
            ? reject(new Error("Late read failure"))
            : resolve([{ result: { artist: "Old Artist", title: "Old Song" } }]);
        });
      });
      await page.locator("#currentTrackButton").click();
      await page.waitForFunction(() => typeof window.finishTrackRead === "function");
      await page.locator("#query").fill(query);
      await page.locator("#submitButton").click();
      await requestArrived;

      if (phase === "completed") {
        releaseResponse();
        await expect(page.locator("#submitButton")).toHaveText("추천 요청");
      } else if (phase === "cancelled") {
        await page.locator("#submitButton").click();
        await expect(page.locator("#statusMessage")).toHaveText("추천 요청을 취소했습니다.");
        releaseResponse();
      }
      const statusBefore = await page.locator("#statusMessage").textContent();
      const buttonBefore = await page.locator("#submitButton").textContent();
      // Drain the read continuation before asserting that it left the newer intent intact.
      await page.evaluate(async (fails) => {
        window.finishTrackRead(fails);
        await new Promise((resolve) => setTimeout(resolve, 0));
      }, readFails);
      await expect(page.locator("#query")).toHaveValue(query);
      await expect(page.locator("#statusMessage")).toHaveText(statusBefore);
      await expect(page.locator("#submitButton")).toHaveText(buttonBefore);
      if (phase === "pending") {
        await expect(page.locator("#currentTrackButton")).toBeDisabled();
        releaseResponse();
        await expect(page.locator("#seedTitle")).toHaveText("Creep");
        await expect(page.locator("#submitButton")).toHaveText("추천 요청");
      }
      expect(requests).toEqual([query]);
    } catch (error) {
      await captureFailure(page, testInfo);
      throw error;
    } finally {
      releaseResponse();
      await context.close();
    }
  });
}

for (const toggleSelector of ["#settingsToggle", "#settingsPanel > summary"]) {
  test(`manually reopened settings stay open after recommendation (${toggleSelector})`, async ({}, testInfo) => {
    const { context, page } = await launchExtensionPage(testInfo);
    try {
      const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
      await page.route(`${apiBaseUrl}/recommend`, (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recommendationPayload),
      }));
      await page.locator("#backendAccessToken").fill(accessToken);
      await page.locator(toggleSelector).click();
      await expect(page.locator("#settingsPanel")).not.toHaveAttribute("open");
      await page.locator(toggleSelector).click();
      await expect(page.locator("#settingsPanel")).toHaveAttribute("open", "");
      await page.locator("#query").fill(query);
      await page.locator("#submitButton").click();
      await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
      await expect(page.locator("#settingsPanel")).toHaveAttribute("open", "");
    } finally {
      await context.close();
    }
  });
}

test("current-track action reads the track and requests recommendations once", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  const requests = [];
  try {
    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    await page.route(`${apiBaseUrl}/recommend`, async (route) => {
      requests.push(route.request().postDataJSON().query);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(recommendationPayload) });
    });
    await page.locator("#backendAccessToken").fill(accessToken);
    await page.evaluate(() => {
      const send = chrome.runtime.sendMessage.bind(chrome.runtime);
      chrome.runtime.sendMessage = (message, ...args) => message.type === "GET_MUSIC_TAB"
        ? Promise.resolve({ ok: true, tabId: 123 }) : send(message, ...args);
      chrome.scripting.executeScript = async () => [{ result: { artist: "Radiohead", title: "Creep" } }];
    });
    await page.locator("#currentTrackButton").click();
    await expect(page.locator("#seedTitle")).toHaveText("Creep");
    await expect(page.locator("#submitButton")).toHaveText("추천 요청");
    await expect(page.locator("#currentTrackButton")).toBeEnabled();
    await expect(page.locator("#query")).toHaveValue(query);
    expect(requests).toEqual([query]);
  } finally {
    await context.close();
  }
});

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

// 요청을 보내기 전에 걸러낸 문제는 서버 연결 상태와 무관하다. 배지가 "연결
// 실패"로 바뀌면 사용자가 입력란 대신 서버를 의심하게 된다.
test("요청 전 검증 실패는 연결 배지를 바꾸지 않는다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);

  try {
    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
      /\/+$/,
      "",
    );
    assertApiOriginIsAllowed(apiBaseUrl);

    let requestCount = 0;
    await page.route(`${apiBaseUrl}/recommend`, async (route) => {
      requestCount += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(recommendationPayload),
      });
    });

    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);
    await page.locator("#submitButton").click();
    // 먼저 성공시켜 배지를 "연결됨"으로 만든다. 초기값에서 출발하면 배지가
    // 바뀌지 않은 것인지 원래 그랬던 것인지 구분할 수 없다.
    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    expect(requestCount).toBe(1);

    // 주소를 망가뜨린다. 접힌 details 안이라 열어야 채울 수 있다.
    await page.locator("#apiBaseUrl").evaluate((input) => {
      for (
        let element = input.closest("details");
        element;
        element = element.parentElement?.closest("details")
      ) {
        element.open = true;
      }
    });
    await page.locator("#apiBaseUrl").fill("not-a-url");
    await page.locator("#submitButton").click();

    await expect(page.locator("#statusMessage")).toHaveText(
      "백엔드 주소 형식이 올바르지 않습니다.",
    );
    // 요청을 보낸 적이 없으므로 마지막으로 확인된 연결 상태가 그대로 남는다.
    await expect(page.locator("#connectionBadge")).toHaveText("연결됨");
    expect(requestCount).toBe(1);
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

// 새 추천을 기다리는 동안 화면에 남아 있는 결과는 이전 곡의 것이다. 그것을
// 내보내면 새 결과 위로 이전 곡의 매칭 검토 창이 열린다.
test("새 추천 요청 중에는 이전 결과를 내보낼 수 없다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);

  try {
    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
      /\/+$/,
      "",
    );
    assertApiOriginIsAllowed(apiBaseUrl);

    let releaseSecondRequest;
    let requestCount = 0;
    await page.route(`${apiBaseUrl}/recommend`, async (route) => {
      requestCount += 1;
      if (requestCount === 2) {
        await new Promise((resolve) => {
          releaseSecondRequest = resolve;
        });
      }
      await route
        .fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(recommendationPayload),
        })
        .catch(() => {});
    });

    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);
    await page.locator("#submitButton").click();
    await expect(page.locator(".export-button")).toBeEnabled();

    // 두 번째 요청이 응답을 기다리는 동안에도 첫 결과는 화면에 남아 있다.
    await page.locator("#submitButton").click();
    await expect(page.locator("#submitButton")).toHaveText("취소");
    await expect(page.locator(".track-item")).toHaveCount(1);
    await expect(page.locator(".export-button")).toBeDisabled();

    releaseSecondRequest?.();
    await expect(page.locator("#submitButton")).toHaveText("추천 요청");
    await expect(page.locator(".export-button")).toBeEnabled();
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

// 모든 방향이 0곡이어도 응답은 받은 것이다. 첫 진입 안내를 결과 위에 겹치면
// 탭 스트립과 온보딩 범례가 동시에 보인다.
test("모든 버킷이 0곡이어도 첫 진입 안내를 겹치지 않는다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);

  try {
    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
      /\/+$/,
      "",
    );
    assertApiOriginIsAllowed(apiBaseUrl);

    await page.route(`${apiBaseUrl}/recommend`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ...recommendationPayload,
          result: { similar: [], reverse: [], hidden: [] },
        }),
      }),
    );

    await page.locator("#backendAccessToken").fill(accessToken);
    await page.locator("#query").fill(query);
    await page.locator("#submitButton").click();

    await expect(page.locator("#statusMessage")).toContainText(
      "추천 결과가 없습니다",
    );
    await expect(page.locator("#emptyState")).toBeHidden();
    await expect(page.locator("#bucketTabs")).toBeVisible();
    await expect(page.locator(".bucket-empty")).toBeVisible();
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

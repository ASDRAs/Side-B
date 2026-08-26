const { expect, test } = require("@playwright/test");

const { captureFailure, launchExtensionPage } = require("./extension.cjs");

const TOKEN = "side-b-persistence-3f2a";
const recommendationPayload = {
  track_name: "혜성",
  artist: "윤하",
  top_n: 10,
  source_id: "itunes:1",
  album_art_url: null,
  result: { similar: [{ name: "밤편지", artist: "아이유" }] },
};

async function stubBackend(page) {
  const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
    /\/+$/,
    "",
  );
  await page.route(`${apiBaseUrl}/recommend`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(recommendationPayload),
    }),
  );
  await page.route(`${apiBaseUrl}/preview/stream**`, (route) =>
    route.fulfill({ status: 200, contentType: "audio/mpeg", body: "" }),
  );
}

/** 같은 프로필에서 패널 문서를 다시 연다. 패널을 닫았다 여는 것과 같다. */
async function reopenPanel(context, page) {
  const url = page.url();
  await page.close();
  const next = await context.newPage();
  await next.goto(url);
  await next.waitForSelector("#apiBaseUrl", { state: "attached" });
  await stubBackend(next);
  return next;
}

test("토큰과 검색어가 패널을 다시 열어도 남는다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let panel = page;

  try {
    await panel.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panel);
    await expect(panel.locator("#tokenStatus")).toHaveText("저장된 토큰 없음");

    // 폼 제출은 토큰을 따로 저장하므로 "입력 직후 닫기"를 가리지 못한다.
    // 여기서는 입력만 하고 blur도 제출도 없이 곧바로 닫는다.
    await panel.locator("#backendAccessToken").fill(TOKEN);
    panel = await reopenPanel(context, panel);
    await expect(panel.locator("#backendAccessToken")).toHaveValue(TOKEN);

    await panel.locator("#query").fill("윤하 - 혜성");
    await panel.locator("#submitButton").click();
    await panel.waitForSelector(".bucket");

    panel = await reopenPanel(context, panel);

    await expect(panel.locator("#backendAccessToken")).toHaveValue(TOKEN);
    await expect(panel.locator("#tokenStatus")).toHaveText("저장됨 · ••••3f2a");
    await expect(panel.locator("#query")).toHaveValue("윤하 - 혜성");
    await expect(panel.locator("#queryHistory option")).toHaveCount(1);
    // 토큰이 있으면 설정을 접어 둔다.
    await expect(panel.locator("#settingsPanel")).not.toHaveAttribute("open", "");
  } catch (error) {
    await captureFailure(panel, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

test("삭제한 토큰은 다시 열어도 살아나지 않는다", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let panel = page;

  try {
    await panel.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panel);
    await panel.locator("#backendAccessToken").fill(TOKEN);
    await expect(panel.locator("#tokenStatus")).toHaveText("저장됨 · ••••3f2a");

    await panel.locator("#tokenClearButton").click();
    await expect(panel.locator("#tokenStatus")).toHaveText("저장된 토큰 없음");

    panel = await reopenPanel(context, panel);

    await expect(panel.locator("#backendAccessToken")).toHaveValue("");
    await expect(panel.locator("#tokenStatus")).toHaveText("저장된 토큰 없음");
  } catch (error) {
    await captureFailure(panel, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

test("한 패널에서 지운 토큰이 다른 패널에도 반영된다", async ({}, testInfo) => {
  // 창마다 사이드 패널이 따로 뜬다. 한쪽에 낡은 토큰이 남으면 그쪽 추천 요청이
  // 그 값을 다시 저장해 삭제가 되돌아간다.
  const { context, page: panelA } = await launchExtensionPage(testInfo);

  try {
    await panelA.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panelA);
    await panelA.locator("#backendAccessToken").fill(TOKEN);
    await expect(panelA.locator("#tokenStatus")).toHaveText("저장됨 · ••••3f2a");

    const panelB = await context.newPage();
    await panelB.goto(panelA.url());
    await panelB.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panelB);
    await expect(panelB.locator("#backendAccessToken")).toHaveValue(TOKEN);

    await panelA.locator("#tokenClearButton").click();

    await expect(panelB.locator("#backendAccessToken")).toHaveValue("");
    await expect(panelB.locator("#tokenStatus")).toHaveText("저장된 토큰 없음");
  } catch (error) {
    await captureFailure(panelA, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});


test("다른 패널의 삭제는 입력 중이어도 반영된다", async ({}, testInfo) => {
  // 활성 입력이라고 건너뛰면 그 패널의 다음 요청이 낡은 값을 다시 저장한다.
  const { context, page: panelA } = await launchExtensionPage(testInfo);

  try {
    await panelA.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panelA);
    await panelA.locator("#backendAccessToken").fill(TOKEN);

    const panelB = await context.newPage();
    await panelB.goto(panelA.url());
    await panelB.waitForSelector("#apiBaseUrl", { state: "attached" });
    await stubBackend(panelB);

    // 토큰이 있으면 설정이 접힌 채로 열린다. 입력란을 드러내야 편집할 수 있다.
    await panelB.locator("#settingsPanel").evaluate((element) => {
      element.open = true;
    });
    // B에서 편집 중인 상태를 만든다. fill이 포커스까지 준다.
    await panelB.locator("#backendAccessToken").fill("in-progress-replacement");
    await expect(panelB.locator("#backendAccessToken")).toBeFocused();

    await panelA.locator("#tokenClearButton").click();

    await expect(panelB.locator("#backendAccessToken")).toHaveValue("");
    await expect(panelB.locator("#tokenStatus")).toHaveText("저장된 토큰 없음");

    // B가 추천을 요청해도 지워진 토큰이 되살아나지 않는다.
    await panelB.locator("#query").fill("윤하 - 혜성");
    await panelB.locator("#submitButton").click();
    await panelB.waitForTimeout(500);
    const stored = await panelB.evaluate(() =>
      chrome.storage.local.get("backendAccessToken"),
    );
    expect(stored.backendAccessToken ?? "").toBe("");
  } catch (error) {
    await captureFailure(panelA, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

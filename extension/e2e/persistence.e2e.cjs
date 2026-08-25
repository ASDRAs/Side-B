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

    // blur 없이 입력만 하고 바로 패널을 닫는다. change 이벤트만으로 저장하면
    // 여기서 유실된다.
    await panel.locator("#backendAccessToken").fill(TOKEN);
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

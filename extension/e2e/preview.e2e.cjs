const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

test("stopping a loading preview is not reported as a playback failure", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let release;
  const mediaGate = new Promise((resolve) => { release = resolve; });
  try {
    const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    await page.route(`${base}/recommend`, (route) => route.fulfill({ json: {
      track_name: "Creep", artist: "Radiohead",
      result: { similar: [{ name: "Karma Police", artist: "Radiohead" }], reverse: [], hidden: [] },
    } }));
    await page.route(`${base}/preview/stream?**`, async (route) => {
      await mediaGate;
      await route.abort().catch(() => {});
    });
    await page.locator("#backendAccessToken").fill("fixture-token");
    await page.locator("#query").fill("Radiohead - Creep");
    await page.locator("#submitButton").click();
    const play = page.locator("#seedPlayButton");
    await expect(play).toBeVisible();
    await play.click();
    await expect.poll(() => page.locator("#seedPreview").evaluate((audio) => audio.paused)).toBe(false);
    await expect(play).toHaveAttribute("aria-label", "미리 듣기 일시정지");
    await play.click();
    await expect.poll(() => page.locator("#seedPreview").evaluate((audio) => audio.paused)).toBe(true);
    await expect(play).toHaveAttribute("aria-label", "미리 듣기");
    await expect(page.locator("#seedPreviewNote")).toBeHidden();
  } finally {
    release();
    await context.close();
  }
});

test("a failed preview download still reports an error", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    await page.route(`${base}/recommend`, (route) => route.fulfill({ json: {
      track_name: "Creep", artist: "Radiohead",
      result: { similar: [], reverse: [], hidden: [] },
    } }));
    await page.route(`${base}/preview/stream?**`, (route) => route.abort());
    await page.locator("#backendAccessToken").fill("fixture-token");
    await page.locator("#query").fill("Radiohead - Creep");
    await page.locator("#submitButton").click();
    await page.locator("#seedPlayButton").click();
    await expect(page.locator("#seedPreviewNote")).toBeVisible();
    await expect(page.locator("#seedPreviewNote")).toHaveText("미리 듣기를 불러오지 못했습니다.");
  } finally { await context.close(); }
});

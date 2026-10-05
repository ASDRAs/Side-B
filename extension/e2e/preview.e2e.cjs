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
    // Bytes are fetched with credentials before playback; a second press cancels.
    await expect(play).toHaveAttribute("aria-label", "미리 듣기 불러오기 취소");
    await play.click();
    await expect(play).toHaveAttribute("aria-label", "미리 듣기");
    await expect(page.locator("#seedPreviewNote")).toBeHidden();
    expect(await page.locator("#seedPreview").getAttribute("src")).toBeNull();
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

function silentWav(samples = 800) {
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + samples * 2, 4); buffer.write("WAVE", 8);
  buffer.write("fmt ", 12); buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(8000, 24); buffer.writeUInt32LE(16000, 28); buffer.writeUInt16LE(2, 32); buffer.writeUInt16LE(16, 34);
  buffer.write("data", 36); buffer.writeUInt32LE(samples * 2, 40);
  return buffer;
}

test("preview audio plays from a Blob fetched with a header credential", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    const seen = [];
    await page.route(`${base}/recommend`, (route) => route.fulfill({ json: {
      track_name: "Creep", artist: "Radiohead", source_id: "deezer:42",
      result: { similar: [], reverse: [], hidden: [] },
    } }));
    await page.route(`${base}/preview/stream?**`, (route) => {
      seen.push({ url: route.request().url(), headers: route.request().headers() });
      return route.fulfill({ status: 200, contentType: "audio/wav", body: silentWav() });
    });
    await page.locator("#backendAccessToken").fill("fixture-token");
    await page.locator("#query").fill("Radiohead - Creep");
    await page.locator("#submitButton").click();
    await page.locator("#seedPlayButton").click();
    await expect.poll(() => page.locator("#seedPreview").getAttribute("src")).toMatch(/^blob:/);
    expect(seen[0].url).toBe(`${base}/preview/stream?provider=deezer&provider_track_id=42`);
    expect(seen[0].url).not.toContain("fixture-token");
    expect(seen[0].headers["x-side-b-access-token"]).toBe("fixture-token");
  } finally { await context.close(); }
});

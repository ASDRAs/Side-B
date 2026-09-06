const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

test("saved settings, real HTTP provider and Web Audio apply genre EQ and recover from failure", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  let audio;
  try {
    const [worker] = context.serviceWorkers();
    await worker.evaluate(() => chrome.storage.local.set({ backendAccessToken: "fixture-team-token" }));
    await context.route("https://music.youtube.com/**", (route) => route.fulfill({
      contentType: "text/html", body: '<!doctype html><title>Music fixture</title><script>navigator.mediaSession.metadata = new MediaMetadata({title:"Girls On Top",artist:"BoA"});</script>',
    }));
    const music = await context.newPage();
    await music.goto("https://music.youtube.com/watch?v=fixture-boa");
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ url: "https://music.youtube.com/*" }))[0].id);
    let status = 200;
    const requests = [];
    await context.route("**/genre-classification", async (route) => {
      requests.push(route.request().postDataJSON());
      expect(route.request().headers()["x-side-b-access-token"]).toBe("fixture-team-token");
      await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(status === 200 ?
        { genre: "dance", score: -0.1, model_version: "fixture-v1" } : { detail: { code: "genre_unauthorized", message: "Fixture invalid token" } }) });
    });
    audio = await context.newPage();
    await audio.goto(new URL("offscreen.html", page.url()).href);
    await audio.evaluate(async (tabId) => {
      // Replace only capture acquisition; settings bridge, HTTP adapter, track
      // reads, ownership checks and actual Web Audio nodes remain production code.
      globalThis.fixtureInputContext = new AudioContext();
      const destination = fixtureInputContext.createMediaStreamDestination();
      createTabMediaStream = async () => destination.stream;
      await startEq({ streamId: "fixture", tabId, mode: "auto" });
    }, tabId);
    await expect.poll(() => audio.evaluate(() => getState().status)).toBe("applied");
    expect(await audio.evaluate(() => ({ genre: getState().genre, bands: filterNodes.map(({ node }) => node.gain.value) })))
      .toEqual({ genre: "dance", bands: [2, 0, -1, 1, 1] });
    expect(requests).toEqual([{ track_name: "Girls On Top", artist: "BoA" }]);
    await expect(page.locator("#eqTestStatus")).toHaveText("dance EQ 적용 중");

    status = 401;
    await audio.evaluate(() => setEqMode("auto"));
    await expect.poll(() => audio.evaluate(() => getState().status)).toBe("unavailable");
    expect(await audio.evaluate(() => filterNodes.length)).toBe(0);
    await expect.poll(() => audio.evaluate(() => preampNode.gain.value)).toBeCloseTo(1, 3);
    await expect(page.locator("#eqTestStatus")).toContainText("Fixture invalid token");
    for (const width of [280, 480]) {
      await page.setViewportSize({ width, height: 900 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`genre-eq-${width}.png`), fullPage: true });
    }
    await audio.evaluate(async () => { await stopEq(); await fixtureInputContext.close(); });
  } finally {
    await context.close();
  }
});

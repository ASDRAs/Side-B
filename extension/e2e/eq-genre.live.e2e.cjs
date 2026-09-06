const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

test("extension consumes real cloud genre analysis and applies its local EQ curve", async ({}, testInfo) => {
  test.setTimeout(210_000);
  const token = process.env.SIDE_B_E2E_ACCESS_TOKEN?.trim();
  if (!token) throw new Error("SIDE_B_E2E_ACCESS_TOKEN is required for this live test.");
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    const [worker] = context.serviceWorkers();
    await worker.evaluate((backendAccessToken) => chrome.storage.local.set({ backendAccessToken }), token);
    await context.route("https://music.youtube.com/**", (route) => route.fulfill({
      contentType: "text/html", body: '<!doctype html><script>navigator.mediaSession.metadata = new MediaMetadata({title:"Girls On Top",artist:"BoA"});</script>',
    }));
    const music = await context.newPage();
    await music.goto("https://music.youtube.com/watch?v=fixture-boa");
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ url: "https://music.youtube.com/*" }))[0].id);
    const audio = await context.newPage();
    await audio.goto(new URL("offscreen.html", page.url()).href);
    const started = Date.now();
    const responsePromise = audio.waitForResponse((r) => r.url().endsWith("/genre-classification"), { timeout: 175_000 });
    await audio.evaluate(async (tabId) => {
      // Only the playback/capture source is a fixture. Backend resolution,
      // preview download, private CLAP/SVM inference and EQ mapping are real.
      globalThis.fixtureInputContext = new AudioContext();
      const destination = fixtureInputContext.createMediaStreamDestination();
      createTabMediaStream = async () => destination.stream;
      await startEq({ streamId: "fixture", tabId, mode: "auto" });
    }, tabId);
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const prediction = await response.json();
    await expect.poll(() => audio.evaluate(() => getState().status)).toBe("applied");
    const state = await audio.evaluate(() => ({
      ...getState(), bands: filterNodes.map(({ node }) => ({ frequency: node.frequency.value, gain: node.gain.value })),
      expected: SideBEqPresets.forGenre(getState().genre).bands.map(({ frequency, gain }) => ({ frequency, gain })),
    }));
    expect(state.genre).toBe(prediction.genre);
    expect(state.bands).toEqual(state.expected);
    await expect(page.locator("#eqTestStatus")).toHaveText(`${prediction.genre} EQ 적용 중`);
    await testInfo.attach("cloud-genre-eq.json", { body: Buffer.from(JSON.stringify({
      prediction, seconds: (Date.now() - started) / 1000, bands: state.bands,
    }, null, 2)), contentType: "application/json" });
    await audio.evaluate(async () => { await stopEq(); await fixtureInputContext.close(); });
  } finally { await context.close(); }
});

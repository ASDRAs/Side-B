const { expect, test } = require("@playwright/test");
const { launchExtensionPage, captureFailure } = require("./extension.cjs");

async function observeEqPolling(page) {
  await page.evaluate(() => {
    const send = chrome.runtime.sendMessage.bind(chrome.runtime);
    globalThis.eqPolls = 0;
    globalThis.failEqPoll = false;
    chrome.runtime.sendMessage = async (message, ...args) => {
      if (message.type !== "GET_EQ_STATE") return send(message, ...args);
      try {
        if (globalThis.failEqPoll) throw new Error("Fixture state bridge failure");
        return await send(message, ...args);
      } finally {
        globalThis.eqPolls += 1;
      }
    };
  });
}

test("EQ action errors survive unchanged polling and clear on the next EQ action", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await observeEqPolling(page);
    await page.locator("#eqTestButton").click();
    const error = "EQ 적용 실패: 이 창에 YouTube Music 탭을 열어 주세요.";
    await expect(page.locator("#eqTestStatus")).toHaveText(error);
    const initialPolls = await page.evaluate(() => eqPolls);
    await expect.poll(() => page.evaluate(() => eqPolls)).toBeGreaterThanOrEqual(initialPolls + 2);
    await expect(page.locator("#eqTestStatus")).toHaveText(error);

    await page.locator('input[name="eqMode"][value="test"]').check();
    await expect(page.locator("#eqTestStatus")).toHaveText("EQ가 적용되지 않았습니다.");
    await page.locator("#eqTestButton").click();
    await expect(page.locator("#eqTestStatus")).toHaveText(error);
    await page.locator("#eqStopButton").click();
    await expect(page.locator("#eqTestStatus")).toHaveText("EQ가 적용되지 않았습니다.");
  } finally {
    await context.close();
  }
});

test("failed EQ polling cannot erase an action error but an actual pending activation can", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await observeEqPolling(page);
    await page.locator("#eqTestButton").click();
    const error = "EQ 적용 실패: 이 창에 YouTube Music 탭을 열어 주세요.";
    await expect(page.locator("#eqTestStatus")).toHaveText(error);
    const initialPolls = await page.evaluate(() => { failEqPoll = true; return eqPolls; });
    await expect.poll(() => page.evaluate(() => eqPolls)).toBeGreaterThan(initialPolls);
    await expect(page.locator("#eqTestStatus")).toHaveText(error);
    await page.evaluate(() => { failEqPoll = false; });

    await context.route("https://music.youtube.com/**", (route) => route.fulfill({
      contentType: "text/html", body: "<!doctype html><title>EQ fixture</title>",
    }));
    const music = await context.newPage();
    await music.goto("https://music.youtube.com/watch?v=fixture");
    const [worker] = context.serviceWorkers();
    await worker.evaluate(async () => {
      const [tab] = await chrome.tabs.query({ url: "https://music.youtube.com/*" });
      await SideBEq.start({ tabId: tab.id, mode: "test" });
    });
    await expect(page.locator("#eqTestStatus")).toContainText("툴바의 Side-B 아이콘");
  } finally {
    await context.close();
  }
});

test("a stop error survives same-state pushes and polling but clears when the song changes", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    // Fixture only the EQ transport; the real panel renders messages and polls.
    await page.evaluate(() => {
      globalThis.fixtureEqState = {
        ok: true, active: true, capturing: true, tabId: 7, mode: "auto", status: "unavailable",
        track: { videoId: "first", title: "First Song", artist: "Fixture Artist" },
      };
      const send = chrome.runtime.sendMessage.bind(chrome.runtime);
      chrome.runtime.sendMessage = async (message, ...args) => {
        if (message.type === "GET_EQ_STATE") return { ...fixtureEqState, polledAt: Date.now() };
        if (message.type === "STOP_EQ") return { ok: false, error: "Fixture stop failure" };
        return send(message, ...args);
      };
    });
    const [worker] = context.serviceWorkers();
    const state = await page.evaluate(() => fixtureEqState);
    const notify = (next) => worker.evaluate((state) => chrome.runtime.sendMessage({
      target: "eq-ui", type: "EQ_STATE_UPDATED", state,
    }), next);
    await notify(state);
    await expect(page.locator("#eqTrack")).toHaveText("First Song - Fixture Artist");
    await observeEqPolling(page);
    await page.locator("#eqStopButton").click();
    const error = "EQ 해제 실패: Fixture stop failure";
    await expect(page.locator("#eqTestStatus")).toHaveText(error);
    await notify({ ...state, polledAt: 1 });
    const initialPolls = await page.evaluate(() => eqPolls);
    await expect.poll(() => page.evaluate(() => eqPolls)).toBeGreaterThan(initialPolls);
    await expect(page.locator("#eqTestStatus")).toHaveText(error);

    const next = await page.evaluate(() => {
      fixtureEqState.track = { ...fixtureEqState.track, videoId: "second", title: "Second Song" };
      return fixtureEqState;
    });
    await notify(next);
    await expect(page.locator("#eqTrack")).toHaveText("Second Song - Fixture Artist");
    await expect(page.locator("#eqTestStatus")).toHaveText("지원하는 장르 프리셋 없음 · 원음 재생 중");
  } finally {
    await context.close();
  }
});

test("a toolbar activation error stays visible across inactive state polling", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await observeEqPolling(page);
    const [worker] = context.serviceWorkers();
    const notify = (state) => worker.evaluate((state) => chrome.runtime.sendMessage({
      target: "eq-ui", type: "EQ_STATE_UPDATED", state,
    }), state);
    // The panel has already rendered the pending request when the toolbar click
    // fails, and clearing that request is what drops polling back to inactive.
    // Starting from a blank panel would let that drop pass as an unrelated state.
    await notify({ ok: true, active: false, status: "awaiting_activation", tabId: 7, mode: "test" });
    await expect(page.locator("#eqTestStatus")).toContainText("툴바의 Side-B 아이콘");
    // Toolbar failures arrive as background notifications, not button rejections.
    await notify({ ok: false, active: false, status: "error", error: "Fixture activation failure" });
    const initialPolls = await page.evaluate(() => eqPolls);
    await expect.poll(() => page.evaluate(() => eqPolls)).toBeGreaterThanOrEqual(initialPolls + 2);
    await expect(page.locator("#eqTestStatus")).toHaveText("EQ 적용 실패: Fixture activation failure");
    await page.locator("#eqStopButton").click();
    await expect(page.locator("#eqTestStatus")).toHaveText("EQ가 적용되지 않았습니다.");
  } finally {
    await context.close();
  }
});

test("real uninvoked tabCapture requests recover through the music-tab activation UI", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await context.route("https://music.youtube.com/**", (route) => route.fulfill({
      contentType: "text/html",
      body: '<!doctype html><title>EQ fixture</title><script>navigator.mediaSession.metadata = new MediaMetadata({title:"Fixture Song",artist:"Fixture Artist"});</script>',
    }));
    const music = await context.newPage();
    await music.goto("https://music.youtube.com/watch?v=fixture");
    const [worker] = context.serviceWorkers();
    const track = await worker.evaluate(async () => {
      const [tab] = await chrome.tabs.query({ url: "https://music.youtube.com/*" });
      return SideBEq.readTrack(tab.id);
    });
    expect(track.title).toBe("Fixture Song");
    expect(track.videoId).toBe("fixture");

    await page.bringToFront();
    await page.locator('input[name="eqMode"][value="test"]').check();
    await page.locator("#eqTestButton").click();
    await expect(page.locator("#eqTestStatus")).toContainText("툴바의 Side-B 아이콘");
    const pending = await worker.evaluate(async () => (await chrome.storage.session.get("pendingEqActivation")).pendingEqActivation);
    expect(pending.mode).toBe("test");
    expect(pending.tabId).toBeGreaterThan(0);

    for (const width of [320, 480]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(page.locator("#eqTestButton")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`eq-${width}.png`), fullPage: true });
    }
    await page.bringToFront();
    await page.locator("#eqStopButton").click();
    await expect(page.locator("#eqTestStatus")).toHaveText("EQ가 적용되지 않았습니다.");
    expect(await worker.evaluate(async () => (await chrome.storage.session.get("pendingEqActivation")).pendingEqActivation)).toBeUndefined();
  } catch (error) {
    await captureFailure(page, testInfo);
    throw error;
  } finally {
    await context.close();
  }
});

test("real Web Audio keeps flat audio unchanged, applies test EQ and bounds boost headroom", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await page.goto(new URL("offscreen.html", page.url()).href);
    const measurement = await page.evaluate(async () => {
      async function render(preset, frequency) {
        const rate = 48000;
        audioContext = new OfflineAudioContext(1, rate, rate);
        const buffer = audioContext.createBuffer(1, rate, rate);
        const input = buffer.getChannelData(0);
        for (let i = 0; i < rate; i++) input[i] = 0.9 * Math.sin(2 * Math.PI * frequency * i / rate);
        sourceNode = audioContext.createBufferSource();
        sourceNode.buffer = buffer;
        preampNode = audioContext.createGain();
        filterNodes = [];
        connectEqGraph();
        updateEq(preset);
        sourceNode.start();
        const output = (await audioContext.startRendering()).getChannelData(0);
        let inputPower = 0, outputPower = 0, peak = 0, difference = 0;
        for (let i = rate / 2; i < rate; i++) {
          inputPower += input[i] ** 2;
          outputPower += output[i] ** 2;
          peak = Math.max(peak, Math.abs(output[i]));
          difference = Math.max(difference, Math.abs(output[i] - input[i]));
        }
        return { db: 10 * Math.log10(outputPower / inputPower), peak, difference };
      }
      return {
        flat: await render(SideBEqPresets.flat(), 1000),
        test: await render(SideBEqPresets.test(), 1000),
        bass: await render(SideBEqPresets.test(), 80),
        boosted: await render({ preamp: 0, bands: [{ frequency: 31, gain: 12 }, { frequency: 62, gain: 12 }, { frequency: 125, gain: 8 }] }, 61.3),
      };
    });
    expect(measurement.flat.difference).toBeLessThan(1e-6);
    expect(measurement.test.db).toBeCloseTo(-12, 1);
    expect(measurement.bass.db).toBeGreaterThan(-1);
    expect(measurement.boosted.peak).toBeLessThan(0.9);
    console.log("Web Audio EQ measurement:", JSON.stringify(measurement));
    await testInfo.attach("eq-audio-measurement.json", { body: Buffer.from(JSON.stringify(measurement, null, 2)), contentType: "application/json" });
  } finally {
    await context.close();
  }
});

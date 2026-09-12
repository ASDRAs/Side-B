const path = require("node:path");
const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

test("seed artwork, ranked tracks and export controls fit narrow light and dark panels", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    // The packaged bitmap is a deterministic image fixture, not a catalog cover.
    await page.route("https://art.example/cover.png", (route) => route.fulfill({
      contentType: "image/png", path: path.join(__dirname, "../icons/icon-128.png"),
    }));
    const longTitle = "아주긴곡제목으로도레이아웃이늘어나지않아야합니다".repeat(3);
    await page.route(`${base}/recommend`, (route) => route.fulfill({ json: {
      track_name: "사건의 지평선", artist: "윤하", album_art_url: "https://art.example/cover.png",
      result: {
        similar: [
          { name: "혜성", artist: "윤하", album_art_url: null, label: "같은 아티스트" },
          { name: longTitle, artist: "긴 아티스트 이름 ".repeat(10), album_art_url: null },
          { name: "비밀번호 486", artist: "윤하", album_art_url: null },
          { name: "", artist: "Unknown" },
        ],
        reverse: [], opposite: null, hidden: [{ name: "오르트구름", artist: "윤하" }],
      },
    } }));
    await page.locator("#backendAccessToken").fill("fixture-token");
    await page.locator("#query").fill("윤하 - 사건의 지평선");
    await page.locator("#submitButton").click();
    await expect(page.locator(".track-item")).toHaveCount(4);
    await expect(page.locator("#seedArt")).toBeVisible();
    await expect.poll(() => page.locator("#seedArt").evaluate((img) => img.naturalWidth)).toBe(128);
    await expect(page.locator(".rank")).toHaveText(["1", "2", "3", "4"]);
    await expect(page.locator(".track-open")).toHaveCount(3);
    await expect(page.locator(".track-title").nth(1)).toHaveAttribute("title", longTitle);
    await expect(page.locator(".export-button")).toHaveAccessibleName("닮은 곡 · 4곡 플레이리스트로 내보내기");
    await expect(page.locator("#bucketTab-opposite")).toHaveCount(0);
    for (const colorScheme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme });
      for (const width of [280, 360, 480]) {
        await page.setViewportSize({ width, height: 820 });
        await expect(page.locator("#eqTestButton")).toBeVisible();
        const outside = await page.evaluate(() => {
          const selectors = ".header, .query-row, .bucket-tab, .track-item, .track-copy, .track-open, .export-button, .eq-bar";
          return [...document.querySelectorAll(selectors)].filter((node) => {
            const rect = node.getBoundingClientRect();
            return rect.left < 0 || rect.right > innerWidth + 1 || node.scrollWidth > node.clientWidth + 1;
          }).map((node) => node.className);
        });
        expect(outside).toEqual([]);
        const rank = await page.locator(".rank").first().boundingBox();
        expect(rank.width).toBe(44);
        expect(rank.height).toBe(44);
        await page.screenshot({ path: testInfo.outputPath(`recommend-${colorScheme}-${width}.png`) });
      }
    }
    await page.locator("#bucketTab-reverse").click();
    await expect(page.locator(".bucket-empty")).toBeVisible();
    await expect(page.locator(".export-button")).toHaveCount(0);
    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});

test("EQ switch waits for confirmed state, stops capture and retries from details", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await page.evaluate(() => {
      const send = chrome.runtime.sendMessage.bind(chrome.runtime);
      globalThis.eqCommands = [];
      globalThis.eqSnapshot = { ok: true, active: false, capturing: false, status: "inactive" };
      chrome.runtime.sendMessage = (message, ...args) => {
        if (message.type === "GET_EQ_STATE") return Promise.resolve(eqSnapshot);
        if (message.type === "START_EQ") {
          eqCommands.push({ type: message.type, mode: message.mode });
          return new Promise((resolve) => {
            globalThis.finishEqStart = () => {
              eqSnapshot = {
                ok: true, active: true, capturing: true, status: "applied", mode: message.mode, genre: "dance",
                track: { title: "Whiplash", artist: "aespa" },
                bands: [{ frequency: 80, gain: 1.5 }, { frequency: 250, gain: 0 }, { frequency: 1000, gain: -1 }, { frequency: 4000, gain: 1 }, { frequency: 10000, gain: 1 }],
              };
              resolve(eqSnapshot);
            };
          });
        }
        if (message.type === "STOP_EQ") {
          eqCommands.push({ type: message.type });
          eqSnapshot = { ok: true, active: false, capturing: false, status: "inactive", bands: [] };
          return Promise.resolve(eqSnapshot);
        }
        return send(message, ...args);
      };
    });
    const toggle = page.getByRole("switch", { name: "EQ", exact: true });
    await toggle.click();
    await expect(toggle).toBeDisabled();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await page.waitForFunction(() => typeof finishEqStart === "function");
    await page.evaluate(() => finishEqStart());
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await expect(toggle).toHaveAttribute("title", "EQ 끄기");
    await page.locator("#eqDetailsToggle").click();
    await expect(page.locator("#eqBands dd")).toHaveText(["+1.5 dB", "0 dB", "-1 dB", "+1 dB", "+1 dB"]);
    for (const colorScheme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme });
      await page.setViewportSize({ width: 280, height: 640 });
      await expect(page.locator("#eqRetryButton")).toBeVisible();
      expect(await page.locator(".eq-bar").evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath(`eq-detail-${colorScheme}-280.png`) });
    }
    await page.evaluate(() => {
      eqSnapshot = { ...eqSnapshot, active: false, status: "suspended" };
    });
    await expect(page.locator("#eqTestStatus")).toContainText("EQ 상세에서 다시 적용");
    // Suspended output still owns capture; the switch must stop it, not restart it.
    await expect(toggle).toHaveAttribute("aria-checked", "true");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-checked", "false");
    await expect(toggle).toHaveAttribute("title", "EQ 켜기");
    await expect(page.locator("#eqBands")).toBeHidden();
    await page.locator('input[name="eqMode"][value="test"]').check();
    await page.locator("#eqRetryButton").click();
    await expect(page.locator("#eqRetryButton")).toBeDisabled();
    await page.evaluate(() => finishEqStart());
    await expect(page.locator("#eqTestStatus")).toHaveText("테스트 EQ 적용 중 · 1 kHz 감쇠");
    expect(await page.evaluate(() => eqCommands)).toEqual([
      { type: "START_EQ", mode: "auto" }, { type: "STOP_EQ" }, { type: "START_EQ", mode: "test" },
    ]);
  } finally {
    await context.close();
  }
});

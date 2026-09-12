const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

test("redesign connects examples, evidence, rediscovery and settings without losing results", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  const errors = [];
  const queries = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
    await page.locator("#backendAccessToken").fill("fixture-token");
    await page.locator("#settingsPanel > summary").click();
    await page.setViewportSize({ width: 400, height: 780 });
    await page.screenshot({ path: testInfo.outputPath("onboarding.png") });
    await page.route(`${base}/recommend`, (route) => {
      queries.push(route.request().postDataJSON().query);
      return route.fulfill({ json: {
        track_name: queries.length === 1 ? "혜성" : "오르트구름", artist: "윤하",
        result: { similar: [
          { name: "오르트구름", artist: "윤하", popularity: 14, exposure_source: "listeners", match_score: .92, reason_tags: ["같은 아티스트"] },
          { name: "사건의 지평선", artist: "윤하", popularity: null, exposure_source: "none", match_score: .8 },
          { name: "비밀번호 486", artist: "윤하", popularity: 30, exposure_source: "none" },
        ], reverse: [], opposite: null, hidden: [] },
      } });
    });
    await page.locator('[data-query="윤하 - 혜성"]').click();
    await expect(page.locator("#seedTitle")).toHaveText("혜성");
    await expect(page.locator(".track-evidence")).toHaveCount(1);
    await expect(page.getByRole("meter")).toHaveAttribute("value", "14");
    await expect(page.locator(".track-evidence")).toContainText("상대 노출");
    await expect(page.locator("#bucketTab-opposite")).toHaveCount(0);
    await expect(page.locator("#seedPlayButton")).toBeVisible();
    for (const colorScheme of ["light", "dark"]) {
      await page.emulateMedia({ colorScheme });
      for (const width of [280, 400, 480]) {
        await page.setViewportSize({ width, height: 780 });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        expect(await page.locator(".track-copy").evaluateAll((nodes) => nodes.every((node) => node.scrollWidth <= node.clientWidth))).toBe(true);
        await page.screenshot({ path: testInfo.outputPath(`results-${colorScheme}-${width}.png`) });
      }
    }
    await page.locator("#settingsToggle").click();
    await expect(page.locator("#settingsBack")).toBeVisible();
    await expect(page.locator("#query")).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath("settings.png") });
    await page.locator("#settingsBack").click();
    await expect(page.locator("#seedTitle")).toHaveText("혜성");
    await page.locator(".track-recommend").first().click();
    await expect(page.locator("#seedTitle")).toHaveText("오르트구름");
    expect(queries).toEqual(["윤하 - 혜성", "윤하 - 오르트구름"]);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

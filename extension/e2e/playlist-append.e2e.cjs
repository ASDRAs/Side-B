const { expect, test } = require("@playwright/test");
const { launchExtensionPage } = require("./extension.cjs");

const tracks = [
  { name: "First", artist: "Artist", video_id: "abcdefghijk" },
  { name: "Second", artist: "Artist", video_id: "lmnopqrstuv" },
];

async function prepare(page) {
  const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
  await page.route(`${base}/recommend`, (route) => route.fulfill({ json: {
    track_name: "Seed", artist: "Artist", result: { similar: tracks, reverse: [], hidden: [] },
  } }));
  await page.route(`${base}/exports/youtube/matches`, (route) => route.fulfill({ json: {
    requested: 2, bucket: "similar", deduplicated: 0, unmatched: [],
    matched: tracks.map((track, position) => ({ ...track, position,
      youtube_title: `${track.artist} - ${track.name}`, channel_title: "Artist", confidence: 0.99,
    })),
  } }));
  await page.locator("#backendAccessToken").fill("fixture-token");
  await page.locator("#query").fill("Artist - Seed");
  await page.locator("#submitButton").click();
  await page.locator(".export-button").click();
  await expect(page.locator("#youtubeMatchReview")).toBeVisible();
}

test("append uses the real worker, keeps selection on back, and restores the destination", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  const worker = context.serviceWorkers()[0];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  try {
    // Only the Google transport is replaced; the worker message handler and state writes run unchanged.
    await worker.evaluate(() => {
      chrome.identity.getAuthToken = async () => ({ token: "fixture-google-token" });
      const originalFetch = globalThis.fetch;
      globalThis.playlistFixture = { videos: new Set(["abcdefghijk"]), inserted: [], reads: [] };
      globalThis.fetch = async (url, init) => {
        if (!String(url).startsWith("https://www.googleapis.com/youtube/v3/")) return originalFetch(url, init);
        const parsed = new URL(url);
        const fixture = globalThis.playlistFixture;
        const playlist = { id: "PLfixture", snippet: { channelId: "UCfixture", title: "발견한 음악을 계속 모으는 플레이리스트" }, contentDetails: { itemCount: 5000 } };
        let result;
        if (parsed.pathname.endsWith("/channels")) result = { items: [{ id: "UCfixture" }] };
        else if (parsed.pathname.endsWith("/playlists") && init.method === "GET") result = { items: [playlist] };
        else if (parsed.pathname.endsWith("/playlistItems") && init.method === "GET") {
          fixture.reads.push(parsed.searchParams.get("videoId"));
          result = { items: fixture.videos.has(parsed.searchParams.get("videoId")) ? [{ id: "existing" }] : [] };
        } else if (parsed.pathname.endsWith("/playlistItems") && init.method === "POST") {
          const body = JSON.parse(init.body);
          if (body.snippet.playlistId !== "PLfixture") throw new Error("Wrong destination");
          const id = body.snippet.resourceId.videoId;
          fixture.videos.add(id);
          fixture.inserted.push(id);
          result = { id: "added" };
        } else throw new Error(`Unexpected Google call: ${init.method} ${url}`);
        return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
      };
    });
    await prepare(page);
    await page.locator('#youtubeMatchList input[data-index="0"]').uncheck();
    await page.locator("#youtubeMatchConfirm").click();
    await page.locator('[name="playlistDestinationMode"][value="append"]').check();
    await page.locator('[name="existingPlaylist"][value="PLfixture"]').check();
    await expect(page.locator("#playlistSkipExisting")).toBeChecked();
    await page.locator("#youtubeMatchBack").click();
    await expect(page.locator('#youtubeMatchList input[data-index="0"]')).not.toBeChecked();
    await page.locator('#youtubeMatchList input[data-index="0"]').check();
    await page.locator("#youtubeMatchConfirm").click();
    await expect(page.locator('[name="existingPlaylist"][value="PLfixture"]')).toBeChecked();
    for (const width of [280, 480]) {
      await page.setViewportSize({ width, height: 720 });
      for (const theme of ["light", "dark"]) {
        await page.emulateMedia({ colorScheme: theme });
        await page.screenshot({ path: testInfo.outputPath(`playlist-destination-${width}-${theme}.png`) });
        const bounds = await page.locator("#youtubeMatchReview").evaluate((element) => ({
          scroll: element.scrollWidth, client: element.clientWidth, bottom: element.getBoundingClientRect().bottom,
        }));
        expect(bounds.scroll).toBeLessThanOrEqual(bounds.client + 1);
        expect(bounds.bottom).toBeLessThanOrEqual(720);
      }
    }
    await page.locator("#youtubeDestinationConfirm").click();
    await expect(page.locator("#youtubeExportDetail")).toContainText("1곡 이미 있음");
    await expect(page.locator("#youtubeExportDetail")).toContainText("1/1곡 추가");
    expect(await worker.evaluate(() => playlistFixture.inserted)).toEqual(["lmnopqrstuv"]);
    await page.locator(".export-button").click();
    await page.locator("#youtubeMatchConfirm").click();
    await expect(page.locator('[name="playlistDestinationMode"][value="append"]')).toBeChecked();
    await expect(page.locator('[name="existingPlaylist"][value="PLfixture"]')).toBeChecked();
    await page.locator("#youtubeDestinationConfirm").click();
    await expect(page.locator("#youtubeExportDetail")).toHaveText("2곡 모두 이미 들어 있습니다.");
    expect(await worker.evaluate(() => playlistFixture.inserted)).toEqual(["lmnopqrstuv"]);
    expect(errors).toEqual([]);
  } finally { await context.close(); }
});

test("late playlist listing cannot reopen a cancelled dialog or initiate a write", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await page.evaluate(() => {
      const original = chrome.runtime.sendMessage.bind(chrome.runtime);
      globalThis.fixtureWrites = 0;
      chrome.runtime.sendMessage = (message) => {
        if (message.type === "LIST_YOUTUBE_PLAYLISTS") return new Promise((resolve) => { globalThis.resolvePlaylistFixture = resolve; });
        if (message.type === "CREATE_YOUTUBE_PLAYLIST") { globalThis.fixtureWrites += 1; throw new Error("Unexpected write"); }
        return original(message);
      };
    });
    await prepare(page);
    await page.locator("#youtubeMatchConfirm").click();
    await page.locator('[name="playlistDestinationMode"][value="append"]').check();
    await expect(page.locator("#playlistLoadStatus")).toContainText("불러오는 중");
    await expect(page.locator("#youtubeDestinationConfirm")).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    await page.evaluate(() => resolvePlaylistFixture({ ok: true, channelId: "UCfixture", recentId: "PLfixture", playlists: [{ id: "PLfixture", title: "Late", count: 1 }] }));
    await expect(page.locator("#youtubeExportStatus")).toHaveText("취소됨");
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    expect(await page.evaluate(() => fixtureWrites)).toBe(0);
  } finally { await context.close(); }
});

test("destination picker recovers from errors and empty lists, filters names and sends the selected policy", async ({}, testInfo) => {
  const { context, page } = await launchExtensionPage(testInfo);
  try {
    await page.evaluate(() => {
      const original = chrome.runtime.sendMessage.bind(chrome.runtime);
      let attempts = 0;
      chrome.runtime.sendMessage = async (message) => {
        if (message.type === "LIST_YOUTUBE_PLAYLISTS") {
          attempts += 1;
          if (attempts === 1) return { ok: false, error: "Google 연결이 취소되었습니다." };
          return { ok: true, channelId: "UCfixture", playlists: attempts === 2 ? [] : [
            { id: "PLrun", title: "Running", count: 10 }, { id: "PLcafe", title: "Cafe", count: 20 },
          ] };
        }
        if (message.type === "CREATE_YOUTUBE_PLAYLIST") {
          globalThis.capturedAppendPayload = message.payload;
          return { ok: true, state: { status: "completed", operationId: message.payload.operation_id, added: 2, toAdd: 2 } };
        }
        return original(message);
      };
    });
    await prepare(page);
    await page.locator("#youtubeMatchConfirm").click();
    await page.locator("#playlistTitle").fill("");
    await expect(page.locator("#youtubeDestinationConfirm")).toBeDisabled();
    await page.locator("#playlistTitle").fill("My collection");
    await page.locator("#youtubeMatchBack").click();
    await page.locator("#youtubeMatchConfirm").click();
    await expect(page.locator("#playlistTitle")).toHaveValue("My collection");
    await page.locator('[name="playlistDestinationMode"][value="append"]').check();
    await expect(page.locator("#playlistLoadStatus")).toHaveText("Google 연결이 취소되었습니다.");
    await expect(page.locator("#youtubeDestinationConfirm")).toBeDisabled();
    await page.locator("#playlistReload").click();
    await expect(page.locator("#playlistLoadStatus")).toHaveText("플레이리스트가 없습니다.");
    await expect(page.locator("#youtubeDestinationConfirm")).toBeDisabled();
    await page.locator("#playlistReload").click();
    await expect(page.locator(".playlist-option")).toHaveCount(2);
    await page.locator("#playlistSearch").fill("CAFE");
    await expect(page.locator(".playlist-option")).toHaveCount(1);
    await page.locator('[name="existingPlaylist"][value="PLcafe"]').check();
    await page.locator("#playlistSkipExisting").uncheck();
    await page.locator("#youtubeDestinationConfirm").click();
    await expect(page.locator("#youtubeMatchReview")).toBeHidden();
    const payload = await page.evaluate(() => capturedAppendPayload);
    expect(payload.destination).toEqual({ mode: "append", playlistId: "PLcafe", title: "Cafe", channelId: "UCfixture", skipExisting: false });
    expect(payload.items.map((item) => item.video_id)).toEqual(tracks.map((item) => item.video_id));
  } finally { await context.close(); }
});

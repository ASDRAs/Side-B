const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const BACKGROUND_SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "background.js"),
  "utf8",
);

function response(status, payload, headers = {}) {
  return {
    status,
    ok: status >= 200 && status < 300,
    headers: {
      get(name) {
        return headers[name] || null;
      },
    },
    async json() {
      return payload;
    },
  };
}

function loadBackground(responses, options = {}) {
  const fetchCalls = [];
  const storage = JSON.parse(JSON.stringify(options.storage || {}));
  const authTokens = ["token-1", "token-2"];
  const authCalls = [];
  const removedTokens = [];
  const offscreenCalls = [];

  const chrome = {
    runtime: {
      getURL: (value) => `chrome-extension://test/${value}`,
      getManifest: () => ({
        oauth2: {
          client_id:
            options.clientId || "client.apps.googleusercontent.com",
        },
      }),
      getContexts: async () => [],
      sendMessage: async () => ({ ok: true }),
      onMessage: { addListener() {} },
    },
    offscreen: {
      async createDocument() {
        offscreenCalls.push("create");
      },
      async closeDocument() {
        offscreenCalls.push("close");
      },
    },
    sidePanel: {
      async setPanelBehavior() {},
    },
    action: { onClicked: { addListener() {} } },
    tabs: {
      query: async () => options.musicTabs || [],
      onRemoved: { addListener() {} },
      onUpdated: { addListener() {} },
    },
    tabCapture: {
      getMediaStreamId: async () => "stream-id",
    },
    identity: {
      async getAuthToken(details) {
        authCalls.push(details);
        return { token: authTokens.shift() };
      },
      async removeCachedAuthToken({ token }) {
        removedTokens.push(token);
      },
    },
    storage: {
      session: {
        async get() { return {}; },
        async remove() {},
      },
      local: {
        async set(values) {
          Object.assign(storage, JSON.parse(JSON.stringify(values)));
        },
        async get(key) {
          if (Array.isArray(key)) return Object.fromEntries(key.map((name) => [name, storage[name]]));
          return { [key]: storage[key] };
        },
      },
    },
  };

  const context = vm.createContext({
    chrome,
    console: { log() {}, error() {} },
    Date,
    Promise,
    setTimeout: options.setTimeout || setTimeout,
    clearTimeout,
    URL,
    AbortController,
    fetch: async (url, init) => {
      fetchCalls.push({ url, init });
      const next = responses.shift();
      assert.ok(next, `Unexpected fetch: ${url}`);
      return typeof next === "function" ? next(url, init) : next;
    },
  });
  context.importScripts = (...files) => {
    for (const file of files) vm.runInContext(fs.readFileSync(path.join(__dirname, "..", file), "utf8"), context);
  };
  vm.runInContext(BACKGROUND_SOURCE, context);

  return {
    context,
    fetchCalls,
    storage,
    authCalls,
    removedTokens,
    offscreenCalls,
  };
}

test("EQ configuration bridge only returns settings to the extension offscreen document", async () => {
  const h = loadBackground([], { storage: { backendAccessToken: "fixture-team-token" } });
  const message = { type: "GET_EQ_SETTINGS" };
  await assert.rejects(h.context.handleMessage(message, { url: "https://music.youtube.com/" }));
  await assert.rejects(h.context.handleMessage(message, { url: "chrome-extension://other/offscreen.html" }));
  const result = await h.context.handleMessage(message, { url: "chrome-extension://test/offscreen.html" });
  assert.equal(result.settings.backendAccessToken, "fixture-team-token");
  assert.equal(h.fetchCalls.length, 0);
});

function exportPayload(items = [
  { video_id: "video-1", name: "First", artist: "Artist 1" },
  { video_id: "video-2", name: "Second", artist: "Artist 2" },
]) {
  return {
    operation_id: "operation-1",
    title: "Side-B test",
    description: "Side-B recommendation",
    requested: items.length,
    matched: items.length,
    deduplicated: 0,
    skipped: 0,
    items,
  };
}

function appendPayload(skipExisting = true) {
  return { ...exportPayload(), destination: {
    mode: "append", playlistId: "PLfixture", channelId: "UCfixture", skipExisting,
  } };
}
const ownerResponse = (id = "UCfixture") => response(200, { items: [{ id }] });
const targetPlaylist = { id: "PLfixture", snippet: { channelId: "UCfixture", title: "Saved tracks" } };
const targetResponse = () => response(200, { items: [targetPlaylist] });

test("append checks ownership, skips existing videos and never creates a playlist", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(),
    response(200, { items: [{ id: "existing" }] }), response(200, { items: [] }),
    response(200, { id: "inserted" }),
  ]);
  const { state, ok } = await h.context.createYouTubePlaylist(appendPayload());
  assert.equal(ok, true);
  assert.equal(state.status, "completed");
  assert.equal(state.existing, 1);
  assert.equal(state.added, 1);
  assert.equal(state.toAdd, 1);
  assert.equal(state.title, "Saved tracks");
  const writes = h.fetchCalls.filter((c) => c.init.method === "POST");
  assert.equal(writes.length, 1);
  assert.match(writes[0].url, /\/playlistItems\?/);
  assert.equal(JSON.parse(writes[0].init.body).snippet.resourceId.videoId, "video-2");
  assert.match(h.fetchCalls[2].url, /videoId=video-1&maxResults=1/);
  assert.equal(h.storage.youtubeLastDestination.channelId, "UCfixture");
});

test("all existing videos complete successfully without any writes", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(),
    response(200, { items: [{ id: "a" }] }), response(200, { items: [{ id: "b" }] }),
  ]);
  const result = await h.context.createYouTubePlaylist(appendPayload());
  assert.equal(result.ok, true);
  assert.equal(result.state.status, "completed");
  assert.deepEqual(Array.from(h.authCalls[0].scopes), ["https://www.googleapis.com/auth/youtube.force-ssl"]);
  assert.equal(result.state.added, 0);
  assert.equal(result.state.existing, 2);
  assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 0);
});

test("explicitly allowing repeats bypasses existing-video queries", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(), response(200, { id: "a" }), response(200, { id: "b" })]);
  const { state } = await h.context.createYouTubePlaylist(appendPayload(false));
  assert.equal(state.added, 2);
  assert.equal(state.existing, 0);
  assert.equal(h.fetchCalls.length, 4);
});

test("changed account, deleted and foreign playlists fail before any writes", async () => {
  for (const responses of [
    [ownerResponse("UCother")],
    [ownerResponse(), response(200, { items: [] })],
    [ownerResponse(), response(200, { items: [{ ...targetPlaylist, snippet: { channelId: "UCother" } }] })],
  ]) {
    const h = loadBackground(responses);
    await assert.rejects(h.context.createYouTubePlaylist(appendPayload()));
    assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 0);
    assert.equal(h.storage.youtubeLastDestination, undefined);
  }
});

test("unusable duplicate-check response never means an empty playlist", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(), response(200, {})]);
  await assert.rejects(h.context.createYouTubePlaylist(appendPayload()));
  assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 0);
});

test("playlist catalog paginates, filters ownership and restores channel-scoped destination", async () => {
  const h = loadBackground([ownerResponse(), response(200, {
    items: [targetPlaylist, { id: "LLspecial", snippet: { channelId: "UCfixture" } }], nextPageToken: "next page",
  }), response(200, { items: [
    { id: "PLsecond", snippet: { title: "Second", channelId: "UCfixture" }, contentDetails: { itemCount: 5000 } },
    { id: "PLforeign", snippet: { channelId: "UCother" } },
  ] })], { storage: { youtubeLastDestination: { mode: "append", channelId: "UCfixture", playlistId: "PLfixture" } } });
  const result = await h.context.listYouTubePlaylists();
  assert.equal(result.playlists.length, 2);
  assert.equal(result.playlists[1].count, 5000);
  assert.equal(result.recentId, "PLfixture");
  assert.match(h.fetchCalls[2].url, /pageToken=next%20page/);
  const other = loadBackground([ownerResponse(), targetResponse()], {
    storage: { youtubeLastDestination: { channelId: "UCother", playlistId: "PLfixture" } },
  });
  assert.equal((await other.context.listYouTubePlaylists()).recentId, null);
});

test("catalog rejects repeated page tokens instead of returning a partial catalog", async () => {
  const h = loadBackground([ownerResponse(), response(200, { items: [], nextPageToken: "a" }), response(200, { items: [], nextPageToken: "a" })]);
  await assert.rejects(h.context.listYouTubePlaylists());
  assert.equal(h.fetchCalls.length, 3);
});

test("append does not retry ambiguous inserts and preserves partial success", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(),
    response(200, { id: "a" }), response(503, { error: { message: "Unavailable" } }),
  ]);
  const { state } = await h.context.createYouTubePlaylist(appendPayload(false));
  assert.equal(state.status, "partial");
  assert.equal(state.added, 1);
  assert.equal(state.failed.length, 1);
  assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 2);
});

test("account change during token refresh stops all remaining append writes", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(), response(401, {}), ownerResponse("UCother")]);
  const { state } = await h.context.createYouTubePlaylist(appendPayload(false));
  assert.equal(state.status, "error");
  assert.equal(state.failed.length, 2);
  assert.match(state.failed[0].error, /계정이 변경/);
  assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 1);
  assert.equal(h.removedTokens.length, 1);
});

test("invalid append destination is rejected before OAuth", async () => {
  const h = loadBackground([]);
  await assert.rejects(h.context.createYouTubePlaylist({ ...appendPayload(), destination: { mode: "append", playlistId: "https://example.com" } }));
  assert.equal(h.authCalls.length, 0);
});

test("catalog HTTP requests abort at the per-request budget", async () => {
  const delays = [];
  const h = loadBackground([(_url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("Aborted")), { once: true });
  })], { setTimeout(callback, delay) { delays.push(delay); return setTimeout(callback, 0); } });
  await assert.rejects(h.context.listYouTubePlaylists(), /요청 시간이 초과/);
  assert.deepEqual(delays, [20_000]);
});

test("append network failure stops without sending remaining writes", async () => {
  const h = loadBackground([ownerResponse(), targetResponse(), () => { throw new Error("Network disconnected"); }]);
  const { state } = await h.context.createYouTubePlaylist(appendPayload(false));
  assert.equal(state.status, "error");
  assert.equal(state.failed.length, 2);
  assert.equal(h.fetchCalls.filter((c) => c.init.method === "POST").length, 1);
});

test("creates a private playlist and inserts items serially", async () => {
  const harness = loadBackground([
    response(200, { id: "playlist-id" }),
    response(200, { id: "item-1" }),
    response(200, { id: "item-2" }),
  ]);

  const result = await harness.context.createYouTubePlaylist(exportPayload());

  assert.equal(result.state.status, "completed");
  assert.equal(result.state.added, 2);
  assert.equal(result.state.requested, 2);
  assert.equal(result.state.matched, 2);
  assert.equal(result.state.toAdd, 2);
  assert.equal(result.state.operationId, "operation-1");
  assert.equal(harness.fetchCalls.length, 3);
  assert.match(harness.fetchCalls[0].url, /\/playlists\?part=/);
  assert.match(harness.fetchCalls[1].url, /\/playlistItems\?part=/);
  assert.match(harness.fetchCalls[2].url, /\/playlistItems\?part=/);
  assert.equal(
    JSON.parse(harness.fetchCalls[1].init.body).snippet.resourceId.videoId,
    "video-1",
  );
  assert.equal(
    JSON.parse(harness.fetchCalls[2].init.body).snippet.resourceId.videoId,
    "video-2",
  );
  assert.equal(harness.storage.youtubeExport.status, "completed");
  assert.doesNotMatch(JSON.stringify(harness.storage), /token-1/);
  assert.deepEqual(harness.offscreenCalls, []);
});

test("keeps successful items when one playlist item fails", async () => {
  const harness = loadBackground([
    response(200, { id: "playlist-id" }),
    response(403, {
      error: {
        message: "Video unavailable",
        errors: [{ reason: "videoNotFound" }],
      },
    }),
    response(200, { id: "item-2" }),
  ]);

  const result = await harness.context.createYouTubePlaylist(exportPayload());

  assert.equal(result.state.status, "partial");
  assert.equal(result.state.added, 1);
  assert.equal(result.state.failed.length, 1);
  assert.equal(result.state.failed[0].videoId, "video-1");
});

test("removes a rejected token and retries only the failed request", async () => {
  const harness = loadBackground([
    response(401, { error: { message: "Invalid credentials" } }),
    response(200, { id: "playlist-id" }),
    response(200, { id: "item-1" }),
  ]);

  const result = await harness.context.createYouTubePlaylist(
    exportPayload([{ video_id: "video-1", name: "First", artist: "Artist" }]),
  );

  assert.equal(result.state.status, "completed");
  assert.equal(harness.authCalls.length, 2);
  assert.deepEqual(harness.removedTokens, ["token-1"]);
  assert.equal(harness.fetchCalls[0].init.headers.Authorization, "Bearer token-1");
  assert.equal(harness.fetchCalls[1].init.headers.Authorization, "Bearer token-2");
});

test("invalid input does not leave the in-memory export lock active", async () => {
  const harness = loadBackground([
    response(200, { id: "playlist-id" }),
    response(200, { id: "item-1" }),
  ]);

  await assert.rejects(
    harness.context.createYouTubePlaylist({ title: "", items: [] }),
    /제목이 비어/,
  );
  const result = await harness.context.createYouTubePlaylist(
    exportPayload([{ video_id: "video-1", name: "First", artist: "Artist" }]),
  );

  assert.equal(result.state.status, "completed");
});

test("quota failure marks remaining items without more API calls", async () => {
  const harness = loadBackground([
    response(200, { id: "playlist-id" }),
    response(403, {
      error: {
        message: "Quota exceeded",
        errors: [{ reason: "quotaExceeded" }],
      },
    }),
  ]);

  const result = await harness.context.createYouTubePlaylist(exportPayload());

  assert.equal(result.state.status, "error");
  assert.equal(result.ok, false);
  assert.equal(result.state.failed.length, 2);
  assert.equal(harness.fetchCalls.length, 2);
});

for (const reason of [
  "dailyLimitExceeded",
  "rateLimitExceeded",
  "userRateLimitExceeded",
]) {
  test(`stops adding items for ${reason}`, async () => {
    const harness = loadBackground([
      response(200, { id: "playlist-id" }),
      response(403, {
        error: {
          message: "Quota exceeded",
          errors: [{ reason }],
        },
      }),
    ]);

    const result = await harness.context.createYouTubePlaylist(exportPayload());

    assert.equal(result.state.status, "error");
    assert.equal(result.ok, false);
    assert.equal(result.state.failed.length, 2);
    assert.equal(result.state.failed[0].error, "YouTube API 할당량이 소진되었습니다.");
    assert.equal(harness.fetchCalls.length, 2);
  });
}

test("deduplicates identical video ids before inserting playlist items", async () => {
  const harness = loadBackground([
    response(200, { id: "playlist-id" }),
    response(200, { id: "item-1" }),
  ]);

  const result = await harness.context.createYouTubePlaylist(
    exportPayload([
      { video_id: "same-video", name: "First", artist: "Artist 1" },
      { video_id: "same-video", name: "Second", artist: "Artist 2" },
    ]),
  );

  assert.equal(result.state.added, 1);
  assert.equal(result.state.toAdd, 1);
  assert.equal(result.state.deduplicated, 1);
  assert.equal(result.state.skipped, 0);
  assert.equal(harness.fetchCalls.length, 2);
});

test("does not wait inside the worker for a long Retry-After", async () => {
  const delays = [];
  const harness = loadBackground(
    [
      response(
        503,
        { error: { message: "Unavailable" } },
        { "Retry-After": "3600" },
      ),
    ],
    {
      setTimeout(callback, delay) {
        delays.push(delay);
        callback();
        return 1;
      },
    },
  );

  await assert.rejects(
    harness.context.createYouTubePlaylist(
      exportPayload([{ video_id: "video-1", name: "First", artist: "Artist" }]),
    ),
    /Unavailable/,
  );

  assert.deepEqual(delays, []);
  assert.equal(harness.storage.youtubeExport.status, "error");
});

test("retries a short 5xx delay and then completes", async () => {
  const delays = [];
  const harness = loadBackground(
    [
      response(500, { error: { message: "Temporary" } }),
      response(200, { id: "playlist-id" }),
      response(200, { id: "item-1" }),
    ],
    {
      setTimeout(callback, delay) {
        delays.push(delay);
        callback();
        return 1;
      },
    },
  );

  const result = await harness.context.createYouTubePlaylist(
    exportPayload([{ video_id: "video-1", name: "First", artist: "Artist" }]),
  );

  assert.equal(result.state.status, "completed");
  assert.deepEqual(delays, [500]);
});

test("marks any active state as interrupted after a worker restart", async () => {
  const harness = loadBackground([], {
    storage: {
      youtubeExport: {
        status: "adding_items",
        title: "Old export",
        operationId: "operation-old",
        added: 1,
        toAdd: 2,
      },
    },
  });

  const state = await harness.context.getYouTubeExportState();

  assert.equal(state.status, "interrupted");
  assert.match(state.error, /중단/);
  assert.equal(harness.storage.youtubeExport.status, "interrupted");
});

test("rejects an unconfigured OAuth client before making requests", async () => {
  const harness = loadBackground([], { clientId: "REPLACE_WITH_CLIENT_ID" });

  await assert.rejects(
    harness.context.createYouTubePlaylist(
      exportPayload([{ video_id: "video-1", name: "First", artist: "Artist" }]),
    ),
    /OAuth Client ID/,
  );

  assert.equal(harness.fetchCalls.length, 0);
  assert.equal(harness.storage.youtubeExport.status, "error");
});

test("resolves the audible YouTube Music tab for the side panel", async () => {
  const harness = loadBackground([], {
    musicTabs: [
      { id: 11, url: "https://music.youtube.com/watch?v=a", audible: false },
      { id: 22, url: "https://music.youtube.com/watch?v=b", audible: true },
    ],
  });

  const tab = await harness.context.getMusicTab();

  assert.equal(tab.ok, true);
  assert.equal(tab.tabId, 22);
  assert.equal(tab.url, "https://music.youtube.com/watch?v=b");
});

test("reports no tab id when YouTube Music is not open", async () => {
  const harness = loadBackground([], { musicTabs: [] });

  const tab = await harness.context.getMusicTab();

  assert.equal(tab.ok, true);
  assert.equal(tab.tabId, null);
  assert.equal(tab.url, null);
});

test("startEq fails clearly when no YouTube Music tab is open", async () => {
  const harness = loadBackground([], { musicTabs: [] });

  await assert.rejects(
    () => harness.context.SideBEq.start({ mode: "auto" }),
    /YouTube Music 탭을 열어/,
  );
});

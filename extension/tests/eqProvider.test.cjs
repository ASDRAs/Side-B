const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const scripts = path.join(__dirname, "..", "scripts");
async function moduleOf(name) {
  return import(`data:text/javascript;base64,${Buffer.from(fs.readFileSync(path.join(scripts, name), "utf8")).toString("base64")}`);
}
const track = { title: "Girls On Top", artist: "BoA", videoId: "youtube-only-id" };
const result = { genre: "dance", score: -0.1, model_version: "fixture" };

async function harness(options = {}) {
  const calls = [];
  let settings = options.settings ?? { backendAccessToken: "fixture-team-token" };
  let now = Date.now();
  const context = vm.createContext({ URL, console, AbortController, clearTimeout,
    Date: class extends Date { static now() { return now; } },
    setTimeout: (fn, ms) => setTimeout(fn, options.timeoutMs ?? ms),
    chrome: { runtime: { async sendMessage(message) {
      assert.equal(message.type, "GET_EQ_SETTINGS");
      return { ok: true, settings };
    } } },
    async fetch(url, init) {
      calls.push({ url, init });
      if (options.fetch) return options.fetch(url, init);
      return new Response(JSON.stringify(options.result ?? result), { status: options.status || 200, headers: { "Retry-After": "30" } });
    },
  });
  for (const name of ["eqPresets.js", "eqProvider.js"]) vm.runInContext(fs.readFileSync(path.join(scripts, name), "utf8"), context);
  if (!options.unconfigured) {
    context.SideBEqProvider.configure(await moduleOf("apiConfig.js"));
  }
  const controller = new AbortController();
  return { context, calls, controller,
    advance: (ms) => { now += ms; },
    useSettings: (value) => { settings = value; },
    run: (value = track) => context.SideBEqProvider.getPreset(value, { signal: controller.signal }) };
}

test("real provider sends the track and saved team token, never a YouTube ID as catalog ID", async () => {
  const h = await harness();
  const preset = await h.run();
  assert.equal(preset.genre, "dance");
  assert.equal(preset.bands[0].gain, 2);
  assert.equal(h.calls[0].url, "https://side-b-backend-7hmhv6htsa-du.a.run.app/genre-classification");
  assert.deepEqual(JSON.parse(h.calls[0].init.body), { track_name: track.title, artist: track.artist });
  assert.equal(h.calls[0].init.headers["X-Side-B-Access-Token"], "fixture-team-token");
  assert.equal(h.calls[0].init.signal, h.controller.signal);
  assert.equal(h.calls[0].init.redirect, "error");
});

test("all nine genres have valid independently owned presets; unknown genres stay flat", async () => {
  const h = await harness();
  const presets = h.context.SideBEqPresets;
  const genres = ["ballad", "dance", "folk_blues_country", "hiphop", "jazz", "jpop", "pop", "rnb_soul", "rock_metal"];
  assert.equal(new Set(genres.map((genre) => JSON.stringify(presets.forGenre(genre)))).size, 9);
  for (const genre of genres) assert.equal(presets.validate(presets.forGenre(genre)).bands.length, 5);
  const changed = presets.forGenre("dance"); changed.bands[0].gain = 12;
  assert.equal(presets.forGenre("dance").bands[0].gain, 2);
  assert.equal(presets.forGenre("toString"), null);
  assert.equal(await (await harness({ result: { ...result, genre: "unknown" } })).run(), null);
});

for (const [name, options, input] of [
  ["missing token", { settings: {} }, track],
  ["missing artist", {}, { title: "A" }],
  ["remote HTTP", { settings: { apiBaseUrl: "http://example.com", apiBaseUrlStorageVersion: 1, backendAccessToken: "secret" } }, track],
]) test(`${name} cannot issue an analysis request`, async () => {
  const h = await harness(options);
  await assert.rejects(h.run(input));
  assert.equal(h.calls.length, 0);
});

test("explicit local settings are preserved", async () => {
  const h = await harness({ settings: { apiBaseUrl: "http://127.0.0.1:8000", apiBaseUrlStorageVersion: 1 } });
  await h.run();
  assert.equal(h.calls[0].url, "http://127.0.0.1:8000/genre-classification");
});

for (const status of [401, 404, 422, 429, 503, 504]) test(`HTTP ${status} remains an error, never an applied preset`, async () => {
  const h = await harness({ status, result: { detail: status === 422 ? [{ msg: "Invalid artist" }] : { message: "Fixture failure" } } });
  await assert.rejects(h.run(), status === 422 ? /Invalid artist/ : /Fixture failure/);
});

test("a non-JSON infrastructure timeout remains a readable error", async () => {
  const h = await harness({ fetch: async () => new Response("<html>timeout</html>", { status: 504 }) });
  await assert.rejects(h.run(), /백엔드/);
});

test("missing model metadata cannot become an EQ preset", async () => {
  const h = await harness({ result: { genre: "dance", score: 0 } });
  await assert.rejects(h.run(), /응답 형식/);
});

test("track cancellation aborts the actual fetch", async () => {
  let started;
  const ready = new Promise((resolve) => { started = resolve; });
  const h = await harness({ fetch: (_url, { signal }) => new Promise((_, reject) => {
    signal.addEventListener("abort", () => reject(signal.reason), { once: true }); started();
  }) });
  const request = h.run();
  await ready;
  h.controller.abort();
  await assert.rejects(request, { name: "AbortError" });
});

test("an already cancelled request never waits for configuration", async () => {
  const h = await harness({ unconfigured: true, timeoutMs: 0 });
  h.controller.abort();
  await assert.rejects(h.run(), { name: "AbortError" });
});

for (const unconfigured of [false, true]) {
  test(`configuration wait cleans up its abort listener (unconfigured=${unconfigured})`, async () => {
    const h = await harness({ unconfigured, timeoutMs: 0 });
    const { getEventListeners } = require("node:events");
    await h.run().catch(() => {});
    assert.equal(getEventListeners(h.controller.signal, "abort").length, 0);
  });
}

test("429 blocks new tracks and explicit resets until the deadline, without sleeping", async () => {
  let status = 200;
  const h = await harness({ fetch: async () => new Response(JSON.stringify(result), {
    status, headers: { "Retry-After": "60" },
  }) });
  await h.run();
  status = 429;
  await assert.rejects(h.run({ ...track, videoId: "new" }));
  await h.run();
  assert.equal(h.calls.length, 2);
  h.context.SideBEqProvider.resetCache();
  await assert.rejects(h.run());
  h.advance(59_999);
  await assert.rejects(h.run({ ...track, videoId: "third" }));
  assert.equal(h.calls.length, 2);
  h.advance(1);
  status = 200;
  await h.run();
  assert.equal(h.calls.length, 3);
});

for (const [header, wait] of [["invalid", 60_000], ["3600", 300_000]]) {
  test(`429 retry delay is bounded for ${header}`, async () => {
    const h = await harness({ fetch: async () => new Response("{}", { status: 429, headers: { "Retry-After": header } }) });
    await assert.rejects(h.run());
    h.advance(wait - 1);
    await assert.rejects(h.run());
    assert.equal(h.calls.length, 1);
    h.advance(1);
    await assert.rejects(h.run());
    assert.equal(h.calls.length, 2);
  });
}

test("a backend change does not inherit another server's cooldown", async () => {
  const h = await harness({ status: 429 });
  await assert.rejects(h.run());
  h.useSettings({ apiBaseUrl: "http://localhost:8000", apiBaseUrlStorageVersion: 1 });
  await assert.rejects(h.run());
  assert.equal(h.calls.length, 2);
});

test("Retry-After accepts an HTTP date without scheduling a retry", async () => {
  const base = Date.UTC(2026, 0, 1);
  const h = await harness({ fetch: async () => new Response("{}", {
    status: 429, headers: { "Retry-After": new Date(base + 60_000).toUTCString() },
  }) });
  h.advance(base - h.context.Date.now());
  await assert.rejects(h.run());
  h.advance(59_999);
  await assert.rejects(h.run());
  assert.equal(h.calls.length, 1);
  h.advance(1);
  await assert.rejects(h.run());
  assert.equal(h.calls.length, 2);
});

test("an automatic track change reuses the genre instead of re-analysing", async () => {
  const h = await harness();
  const first = await h.run();
  const second = await h.run();
  assert.equal(h.calls.length, 1);
  assert.deepEqual(second, first);
});

test("resetCache forces revalidation so a failing backend cannot hide behind a hit", async () => {
  const h = await harness();
  await h.run();
  h.context.SideBEqProvider.resetCache();
  await h.run();
  assert.equal(h.calls.length, 2);
});

test("a cached genre is not reused after the backend address changes", async () => {
  const h = await harness();
  await h.run();
  h.useSettings({ apiBaseUrl: "http://127.0.0.1:8000", apiBaseUrlStorageVersion: 1 });
  await h.run();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].url, "http://127.0.0.1:8000/genre-classification");
});

test("the cache is bounded and evicts the oldest track first", async () => {
  const h = await harness();
  for (let index = 0; index <= 200; index += 1) await h.run({ ...track, videoId: `id-${index}` });
  assert.equal(h.calls.length, 201);
  await h.run({ ...track, videoId: "id-200" });
  assert.equal(h.calls.length, 201, "the newest track stays cached");
  await h.run({ ...track, videoId: "id-0" });
  assert.equal(h.calls.length, 202, "the oldest track was evicted");
});

test("an unconfigured provider fails fast instead of waiting out the analysis timeout", async () => {
  const h = await harness({ unconfigured: true, timeoutMs: 0 });
  await assert.rejects(h.run(), /EQ 설정을 불러오지 못했습니다/);
  assert.equal(h.calls.length, 0);
});

test("cancelling while unconfigured rejects immediately", async () => {
  const h = await harness({ unconfigured: true, timeoutMs: 60_000 });
  const request = h.run();
  h.controller.abort();
  await assert.rejects(request, { name: "AbortError" });
});

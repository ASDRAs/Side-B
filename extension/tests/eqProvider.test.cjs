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
  const settings = options.settings ?? { backendAccessToken: "fixture-team-token" };
  const context = vm.createContext({ URL, console, AbortController,
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
  context.SideBEqProvider.configure({ ...await moduleOf("apiConfig.js"), apiErrorMessage: (await moduleOf("youtubeExportView.js")).apiErrorMessage });
  const controller = new AbortController();
  return { context, calls, controller, run: (value = track) => context.SideBEqProvider.getPreset(value, { signal: controller.signal }) };
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

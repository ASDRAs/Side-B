const assert = require("node:assert/strict");
const test = require("node:test");

const load = () => import("../scripts/previewPlayer.js");
const API = "https://api.example";
const URL_ = `${API}/preview/stream?provider=deezer&provider_track_id=1`;
const audio = (bytes = 4, headers = {}) => new Response(new Uint8Array(bytes), {
  status: 200, headers: { "Content-Type": "audio/mpeg", ...headers },
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

function session(createPreviewSession, fetchPreview, maxBytes) {
  const created = [], revoked = [];
  const value = createPreviewSession({
    fetchPreview,
    maxBytes,
    createObjectURL: (blob) => { const url = `blob:${created.length}:${blob.size}:${blob.type}`; created.push(url); return url; },
    revokeObjectURL: (url) => revoked.push(url),
  });
  return { value, created, revoked };
}

test("preview bytes become a revocable Blob URL", async () => {
  const { createPreviewSession } = await load();
  const s = session(createPreviewSession, async () => audio(16));
  assert.equal(await s.value.load(URL_), "blob:0:16:audio/mpeg");
  assert.equal(s.value.objectUrl, "blob:0:16:audio/mpeg");
  s.value.cancel();
  assert.deepEqual(s.revoked, ["blob:0:16:audio/mpeg"]);
  assert.equal(s.value.objectUrl, null);
});

test("switching tracks revokes the previous Blob and aborts its request", async () => {
  const { createPreviewSession } = await load();
  const held = deferred();
  const signals = [];
  let calls = 0;
  const s = session(createPreviewSession, (_url, init) => {
    signals.push(init.signal);
    return ++calls === 1 ? audio(4) : held.promise;
  });
  await s.value.load(URL_);
  const second = s.value.load(`${URL_}2`);
  assert.deepEqual(s.revoked, ["blob:0:4:audio/mpeg"]);
  s.value.cancel(); // logout or block while the second request is loading
  assert.equal(signals[1].aborted, true);
  held.resolve(audio(8));
  await assert.rejects(second, { name: "AbortError" });
  assert.equal(s.created.length, 1, "a late response must not create a Blob URL");
  assert.equal(s.value.loading, false);
});

test("oversized or non-audio responses are refused", async () => {
  const { createPreviewSession } = await load();
  const declared = session(createPreviewSession, async () => audio(4, { "Content-Length": "999" }), 100);
  await assert.rejects(declared.value.load(URL_), /너무 큽니다/);
  const streamed = session(createPreviewSession, async () => audio(101), 100);
  await assert.rejects(streamed.value.load(URL_), /너무 큽니다/);
  const html = session(createPreviewSession, async () => new Response("<html>", { headers: { "Content-Type": "text/html" } }));
  await assert.rejects(html.value.load(URL_), /오디오가 아닙니다/);
  for (const s of [declared, streamed, html]) assert.deepEqual(s.created, []);
});

test("an approval denial keeps its status and payload for the caller", async () => {
  const { createPreviewSession, PreviewLoadError } = await load();
  const s = session(createPreviewSession, async () => new Response(JSON.stringify({
    detail: { code: "access_not_approved", access_status: "blocked" },
  }), { status: 403 }));
  const error = await s.value.load(URL_).catch((caught) => caught);
  assert.ok(error instanceof PreviewLoadError);
  assert.equal(error.status, 403);
  assert.equal(error.payload.detail.access_status, "blocked");
});

test("the preview request carries the credential in a header, never in the URL", async () => {
  const { createPreviewSession } = await load();
  const seen = [];
  global.chrome = { runtime: { sendMessage: async () => ({ ok: true, credential: {
    mode: "firebase", sessionGeneration: "s", headers: { Authorization: "Bearer secret-id-token" },
  } }) } };
  const { authenticatedFetch } = await import("../scripts/authClient.js");
  const s = session(createPreviewSession, (url, init) => authenticatedFetch(async (target, options) => {
    seen.push({ target, authorization: options.headers.get("Authorization") });
    return audio(4);
  }, url, init, { apiBaseUrl: API }));
  await s.value.load(URL_);
  assert.equal(seen[0].target, URL_);
  assert.doesNotMatch(seen[0].target, /secret|token|Bearer/i);
  assert.equal(seen[0].authorization, "Bearer secret-id-token");
  assert.doesNotMatch(s.created[0], /secret/);
});

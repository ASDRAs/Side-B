const assert = require("node:assert/strict");
const test = require("node:test");
const API = "https://api.example";
const credential = (token, generation = "session-a") => ({ mode: "firebase", sessionGeneration: generation, headers: { Authorization: `Bearer ${token}` } });

async function harness(answers) {
  const messages = [];
  global.chrome = { runtime: { sendMessage: async (message) => {
    messages.push(message);
    const next = answers.shift();
    if (typeof next === "function") return next(message);
    return { ok: true, credential: next };
  } } };
  const { authenticatedFetch } = await import("../scripts/authClient.js");
  return { messages, run: (fetch, init = {}, url = `${API}/recommend`) => authenticatedFetch(fetch, url, init, { apiBaseUrl: API, legacyToken: "old" }) };
}

test("request auth strips all previous credentials and never follows redirects", async () => {
  const h = await harness([credential("fresh")]);
  await h.run(async (_url, init) => {
    assert.equal(init.headers.get("Authorization"), "Bearer fresh");
    assert.equal(init.headers.get("X-Side-B-Access-Token"), null);
    assert.equal(init.headers.get("X-Side-B-Export-Token"), null);
    assert.equal(init.headers.get("Content-Type"), "application/json");
    assert.equal(init.redirect, "error");
    return new Response("{}", { status: 200 });
  }, { headers: { authorization: "old", "X-Side-B-Access-Token": "old", "X-Side-B-Export-Token": "old", "Content-Type": "application/json" } });
});

test("401 gets exactly one refresh and forwards the rejected token", async () => {
  const h = await harness([credential("old"), credential("new")]);
  let calls = 0;
  const result = await h.run(async () => { calls++; return new Response("{}", { status: 401 }); });
  assert.equal(result.status, 401);
  assert.equal(calls, 2);
  assert.equal(h.messages[1].forceRefresh, true);
  assert.equal(h.messages[1].rejectedToken, "old");
});

for (const status of [200, 403, 429, 503]) test(`${status} never triggers a refresh or interactive login`, async () => {
  const h = await harness([credential("fresh")]);
  await h.run(async () => new Response("{}", { status }));
  assert.equal(h.messages.length, 1);
  assert.equal(h.messages[0].type, "AUTH_GET_CREDENTIAL");
});

test("a changed account cannot replay the previous account request", async () => {
  const h = await harness([credential("old"), credential("new", "session-b")]);
  let calls = 0;
  await assert.rejects(h.run(async () => { calls++; return new Response("{}", { status: 401 }); }), /계정이 변경/);
  assert.equal(calls, 1);
});

test("an untrusted request URL cannot even obtain a credential", async () => {
  const h = await harness([]);
  await assert.rejects(h.run(() => assert.fail("must not fetch"), {}, "https://evil.example/recommend"));
  assert.equal(h.messages.length, 0);
});

test("aborting during worker credential lookup prevents the HTTP request", async () => {
  const controller = new AbortController();
  const h = await harness([() => { controller.abort(); return { ok: true, credential: credential("fresh") }; }]);
  await assert.rejects(h.run(() => assert.fail("must not fetch"), { signal: controller.signal }), { name: "AbortError" });
});

test("worker missing-legacy-token errors prevent a match request", async () => {
  const h = await harness([() => ({ ok: false, error: "팀 백엔드 토큰을 입력하세요." })]);
  await assert.rejects(h.run(() => assert.fail("must not fetch"), {}, `${API}/exports/youtube/matches`), /토큰/);
});

test("cancellation does not wait for a stalled worker credential lookup", async () => {
  const controller = new AbortController();
  const h = await harness([() => new Promise(() => {})]);
  const pending = h.run(() => assert.fail("must not fetch"), { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
});

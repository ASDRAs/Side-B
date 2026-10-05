// Production authClient -> background bridge -> dist worker, with local HTTP/persistence.
const assert = require("node:assert/strict");
const test = require("node:test");
const { UserImpl } = require("../node_modules/@firebase/auth/dist/web-extension-cjs/internal.js");
const { createSubscribe } = require("@firebase/util");
const { repairHarness, ORIGIN, settle, deferred, me, response } = require("./helpers/repairHarness.cjs");

async function signedIn(status = "unregistered") {
  const h = repairHarness();
  h.responses.push(response(200, { mode: "firebase", firebase_project_id: "gen-lang-client-0392647514" }),
    me("A", status, false));
  await h.send({ type: "AUTH_SIGN_IN" });
  return h;
}

async function sdkRenewal(t, h, { forceOnly = false, gate = null } = {}) {
  let notifier, notifications = 0, writes = 0;
  const calls = [];
  const subscribe = createSubscribe((proxy) => { notifier = proxy; });
  t.after(subscribe((user) => { notifications++; h.observe(user); }));
  await settle();
  h.a.auth = h.auth;
  h.a.accessToken = "fixture-before";
  h.a.stsTokenManager = { getToken: async (_auth, force) => {
    calls.push({ force });
    if (forceOnly && !force) return h.a.accessToken;
    if (gate) await gate.promise;
    return "fixture-renewed";
  } };
  h.auth._persistUserIfCurrent = async () => { writes++; };
  h.auth._notifyListenersIfCurrent = (user) => {
    if (user === h.auth.currentUser) notifier.next(user);
  };
  h.sdk.getIdToken = (user, force) => user === h.a
    ? UserImpl.prototype.getIdToken.call(user, force) : Promise.resolve(`fixture-${user.uid}`);
  return { calls, notifications: () => notifications, writes: () => writes };
}

async function client(t, h) {
  const previous = globalThis.chrome;
  globalThis.chrome = { runtime: { sendMessage: h.send } };
  t.after(() => { globalThis.chrome = previous; });
  return import("../scripts/authClient.js");
}

function queueHttp(h, handler, count = 6) {
  for (let i = 0; i < count; i++) h.responses.push(handler);
}

test("request repair: first click posts once and becomes pending during real SDK token observer", async (t) => {
  const h = await signedIn();
  const sdk = await sdkRenewal(t, h);
  const api = await client(t, h);
  const before = h.fetchCalls.length;
  const generation = h.manager.state().sessionGeneration;
  queueHttp(h, (url) => url.endsWith("/access/request")
    ? response(200, { access_status: "pending", access_requested_at: "2026-10-05T01:00:00Z" })
    : me("A", "unregistered", false));
  const result = await api.requestAccess();
  await settle();
  const posts = h.fetchCalls.slice(before).filter((call) => call.url.endsWith("/access/request"));
  assert.equal(sdk.notifications(), 1);
  assert.equal(sdk.writes(), 1);
  assert.equal(posts.length, 1, "the first click must actually POST");
  assert.equal(posts[0].init.method, "POST");
  assert.equal(posts[0].init.body, "{}");
  assert.equal(result.access.status, "pending");
  assert.equal(result.account.uid, "A");
  assert.equal(result.sessionGeneration, generation);
  // Once token acquisition finishes, an ordinary newer observer still wins.
  h.responses.length = 0;
  h.responses.push(me("A", "blocked", false));
  h.observe();
  await settle();
  assert.equal(h.manager.state().access.status, "blocked");
});

test("request repair: normal refresh token observer reuses the explicit status query", async (t) => {
  const h = await signedIn("pending");
  const sdk = await sdkRenewal(t, h);
  const api = await client(t, h);
  const before = h.fetchCalls.length;
  queueHttp(h, () => me("A", "approved", false));
  const result = await api.refreshAccess();
  await settle();
  assert.equal(sdk.notifications(), 1);
  assert.equal(h.fetchCalls.length - before, 1);
  assert.equal(result.access.status, "approved");
});

for (const finalStatus of [200, 401, 403]) {
  test(`request repair: force401 token observer preserves explicit retry HTTP ${finalStatus}`, async (t) => {
    const h = await signedIn("pending");
    const sdk = await sdkRenewal(t, h, { forceOnly: true });
    const api = await client(t, h);
    const before = h.fetchCalls.length;
    queueHttp(h, (_url, init) => init.headers.Authorization === "Bearer fixture-before"
      ? response(401, { detail: { code: "auth_unauthorized" } })
      : finalStatus === 200 ? me("A", "approved", false)
        : response(finalStatus, { detail: { code: "auth_identity_unverified" } }));
    const result = await api.refreshAccess();
    await settle();
    assert.equal(sdk.notifications(), 1);
    assert.equal(sdk.calls.filter((call) => call.force).length, 1);
    assert.equal(h.fetchCalls.length - before, 2, "one original GET and one forced retry");
    assert.equal(result.status, finalStatus === 200 ? "signed_in" : finalStatus === 403 ? "denied" : "error");
    if (finalStatus === 200) assert.equal(result.access.status, "approved");
    else assert.equal(result.account, null);
    assert.equal(h.signouts(), finalStatus === 403 ? 1 : 0);
  });
}

for (const operation of ["requestAccess", "refreshAccess"]) {
  test(`request repair: concurrent ${operation} remains deduplicated across token observer and HTTP`, async (t) => {
    const h = await signedIn(operation === "requestAccess" ? "unregistered" : "pending");
    const token = deferred();
    const http = deferred();
    const sdk = await sdkRenewal(t, h, { forceOnly: operation === "refreshAccess", gate: token });
    const api = await client(t, h);
    const before = h.fetchCalls.length;
    queueHttp(h, (url, init) => url.endsWith("/access/request") ? http.promise
      : init.headers.Authorization === "Bearer fixture-before"
        ? response(401, { detail: { code: "auth_unauthorized" } }) : http.promise);
    const first = api[operation]();
    const second = api[operation]();
    await settle();
    token.resolve();
    await settle();
    const third = api[operation]();
    await settle();
    http.resolve(operation === "requestAccess" ? response(200, { access_status: "pending" })
      : me("A", "approved", false));
    const results = await Promise.all([first, second, third]);
    await settle();
    assert.equal(sdk.notifications(), 1);
    assert.equal(h.fetchCalls.length - before, operation === "requestAccess" ? 1 : 2);
    assert.ok(results.every((state) => state.access.status === (operation === "requestAccess" ? "pending" : "approved")));
    if (operation === "refreshAccess") assert.equal(sdk.calls.filter((call) => call.force).length, 1);
  });
}

for (const operation of ["AUTH_REQUEST_ACCESS", "AUTH_REFRESH_ACCESS"]) {
  for (const boundary of ["account", "same-uid", "logout", "feature403", "admin403", "server"]) {
    test(`request repair: ${boundary} invalidates ${operation} during token acquisition`, async () => {
      const h = await signedIn();
      const token = deferred();
      h.setToken((user) => user === h.a ? token.promise : `fixture-${user.uid}`);
      const before = h.fetchCalls.length;
      // Accept either a discarded snapshot or a session-invalidated rejection.
      const pending = h.send({ type: operation }).then((value) => ({ value }), (error) => ({ error }));
      await settle();
      h.observe();
      await settle();
      if (boundary === "account" || boundary === "same-uid") {
        const next = boundary === "account" ? h.b : { uid: "A", email: "A@example.com" };
        h.responses.push(me(next.uid, "blocked", false));
        h.switchTo(next);
      } else if (boundary === "logout") await h.send({ type: "AUTH_SIGN_OUT" });
      else if (boundary === "server") {
        h.responses.push(response(200, { mode: "legacy" }));
        await h.send({ type: "AUTH_CONFIGURE", apiBaseUrl: "http://localhost:8000" });
      } else {
        h.setToken((user) => `fixture-${user.uid}`);
        h.responses.push(response(503, {}));
        await h.send({ type: "AUTH_REPORT_DENIAL", purpose: boundary === "feature403" ? "feature" : "admin",
          code: boundary === "feature403" ? "access_not_approved" : "admin_required", accessStatus: "blocked",
          apiOrigin: ORIGIN, sessionGeneration: h.manager.state().sessionGeneration });
      }
      await settle();
      const afterBoundary = h.fetchCalls.length;
      const state = JSON.stringify(h.manager.state());
      token.resolve();
      await pending;
      await settle();
      assert.equal(JSON.stringify(h.manager.state()), state);
      assert.equal(h.fetchCalls.length, afterBoundary, "an invalidated operation cannot send after its token arrives");
      assert.equal(h.fetchCalls.slice(before).filter((call) => call.url.endsWith("/access/request")).length, 0);
    });
  }
}

// Login session vs. server approval in the bundled auth worker (dist/authWorker.js).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const ORIGIN = "https://api.example";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
const me = (access, extra = {}) => ({ uid: "user-a", email: "a@example.com", display_name: "A",
  access_status: access, access_store: "firestore", can_manage_access: false,
  access_requested_at: access === "unregistered" ? null : "2026-10-05T01:02:03Z", ...extra });

function harness({ meResponses = [() => json(me("approved"))], request, restored = false, mode = "firebase" } = {}) {
  const calls = [], messages = [], changes = [];
  const user = { uid: "user-a", email: "a@example.com" };
  const auth = { currentUser: restored ? user : null };
  let observer;
  let signouts = 0;
  let meIndex = 0;
  const sdk = {
    initializeApp: (config) => config,
    initializeAuth: () => auth,
    indexedDBLocalPersistence: {},
    GoogleAuthProvider: { credential: () => ({}) },
    onIdTokenChanged: (_auth, callback) => { observer = callback; queueMicrotask(() => callback(auth.currentUser)); },
    signInWithCredential: async () => { auth.currentUser = user; observer(user); return { user }; },
    signOut: async () => { signouts++; auth.currentUser = null; observer(null); },
    getIdToken: async (_user, force) => (force ? "firebase-refreshed" : "firebase-a"),
  };
  const context = vm.createContext({ URL, AbortController, console, clearTimeout, setTimeout });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../dist/authWorker.js"), "utf8"), context);
  const manager = context.SideBAuthBundle.createAuthManager({
    config: { firebase: { apiKey: "public-fixture", authDomain: "fixture.firebaseapp.com", projectId: "fixture", appId: "fixture" },
      trustedBackendOrigins: [ORIGIN], legacyServerOrigins: [] },
    chromeApi: {
      runtime: { sendMessage: async (message) => { messages.push(message); } },
      identity: { getAuthToken: async () => ({ token: "google-signin" }) },
    },
    sdk,
    onChange: (next, previous) => changes.push({ next, previous }),
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/auth/config")) return json({ mode, firebase_project_id: "fixture" });
      if (url.endsWith("/auth/me")) {
        const responder = meResponses[Math.min(meIndex++, meResponses.length - 1)];
        return responder(init);
      }
      if (url.endsWith("/access/request")) return request(init);
      throw new Error(`unexpected ${url}`);
    },
  });
  return {
    manager, calls, messages, changes, auth, sdk,
    signouts: () => signouts,
    meCalls: () => calls.filter(({ url }) => url.endsWith("/auth/me")).length,
    credential: (purpose) => manager.credential({ apiBaseUrl: ORIGIN, purpose }),
    async login() { await manager.configure(ORIGIN); return manager.signIn(); },
  };
}

for (const status of ["unregistered", "pending", "rejected", "blocked"]) {
  test(`${status} keeps the Google session but refuses feature credentials`, async () => {
    const h = harness({ meResponses: [() => json(me(status))] });
    const state = await h.login();
    assert.equal(state.status, "signed_in");
    assert.equal(state.access.status, status);
    assert.equal(h.signouts(), 0);
    await assert.rejects(h.credential("feature"), (error) => error.code === "access_not_approved");
    await assert.rejects(h.credential("admin"), (error) => error.code === "admin_required");
    // The unapproved session may still check or request its own access.
    assert.equal((await h.credential("access")).headers.Authorization, "Bearer firebase-a");
  });
}

test("approved accounts receive feature credentials; unknown purposes are refused", async () => {
  const h = harness();
  await h.login();
  assert.equal((await h.credential("feature")).headers.Authorization, "Bearer firebase-a");
  assert.equal((await h.manager.credential({ apiBaseUrl: ORIGIN })).headers.Authorization, "Bearer firebase-a");
  await assert.rejects(h.credential("superuser"), /허용되지 않은/);
});

test("the administrator menu flag comes only from the server response", async () => {
  const h = harness({ meResponses: [() => json(me("pending", { can_manage_access: true }))] });
  const state = await h.login();
  assert.equal(state.access.canManage, true);
  assert.equal((await h.credential("admin")).headers.Authorization, "Bearer firebase-a");
  await assert.rejects(h.credential("feature"));
});

test("servers without the approval contract keep their allowlist meaning", async () => {
  const h = harness({ meResponses: [() => json({ uid: "user-a", email: "a@example.com" })] });
  const state = await h.login();
  assert.equal(state.access.status, "approved");
  assert.equal(state.access.store, "env");
  assert.equal(state.access.canManage, false);
});

test("unknown access values fail closed instead of opening features", async () => {
  const h = harness({ meResponses: [() => json(me("superuser"))] });
  assert.equal((await h.login()).access.status, "unavailable");
  await assert.rejects(h.credential("feature"));
});

test("identity rejection signs out and reports the account, server and code", async () => {
  const h = harness({ meResponses: [() => json({ detail: { code: "auth_identity_unverified", message: "x" } }, 403)] });
  const state = await h.login();
  assert.equal(state.status, "denied");
  assert.equal(state.access, null);
  assert.equal(h.signouts(), 1);
  assert.match(state.error, /a@example\.com/);
  assert.ok(state.error.includes(ORIGIN));
  assert.match(state.error, /auth_identity_unverified/);
});

test("an approval store outage keeps the session and fails features closed", async () => {
  const h = harness({ meResponses: [() => json({ detail: { code: "access_store_unavailable" } }, 503)] });
  const state = await h.login();
  assert.equal(state.status, "signed_in");
  assert.equal(state.access.status, "unavailable");
  assert.equal(h.signouts(), 0);
  await assert.rejects(h.credential("feature"));
});

test("manual refresh after approval opens features without a new login", async () => {
  const h = harness({ meResponses: [() => json(me("pending")), () => json(me("approved"))] });
  const first = await h.login();
  const refreshed = await h.manager.refreshAccess();
  assert.equal(refreshed.access.status, "approved");
  assert.equal(refreshed.sessionGeneration, first.sessionGeneration);
  assert.equal((await h.credential("feature")).headers.Authorization, "Bearer firebase-a");
});

test("losing approval invalidates the generation and in-flight credentials", async () => {
  const h = harness({ meResponses: [() => json(me("approved")), () => json(me("blocked"))] });
  const first = await h.login();
  const tokenGate = deferred();
  const immediate = h.sdk.getIdToken;
  h.sdk.getIdToken = () => tokenGate.promise; // this credential is still in flight
  const credential = h.credential("feature");
  h.sdk.getIdToken = immediate;
  const state = await h.manager.refreshAccess();
  tokenGate.resolve("late-token");
  assert.equal(state.access.status, "blocked");
  assert.notEqual(state.sessionGeneration, first.sessionGeneration);
  await assert.rejects(credential, /변경/);
  await assert.rejects(h.credential("feature"));
  const change = h.changes.at(-1);
  assert.equal(change.previous.access.status, "approved");
  assert.equal(change.next.access.status, "blocked");
});

test("concurrent refreshes share one status request", async () => {
  const gate = deferred();
  const h = harness({ meResponses: [() => json(me("pending")), () => gate.promise] });
  await h.login();
  const before = h.meCalls();
  const pending = Promise.all([h.manager.refreshAccess(), h.manager.refreshAccess(), h.manager.refreshAccess()]);
  gate.resolve(json(me("approved")));
  await pending;
  assert.equal(h.meCalls() - before, 1);
});

test("refresh distinguishes identity rejection from transient failures", async () => {
  const denied = harness({ meResponses: [() => json(me("approved")), () => json({ detail: { code: "auth_identity_unverified" } }, 403)] });
  await denied.login();
  const state = await denied.manager.refreshAccess();
  assert.equal(state.status, "denied");
  assert.equal(denied.signouts(), 1);

  const flaky = harness({ meResponses: [() => json(me("pending")), () => { throw new TypeError("offline"); }] });
  await flaky.login();
  const kept = await flaky.manager.refreshAccess();
  assert.equal(kept.status, "signed_in");
  assert.equal(kept.access.status, "pending");
  assert.ok(kept.access.error);
  assert.equal(flaky.signouts(), 0);
});

test("refresh retries a rejected token once with a forced refresh", async () => {
  const tokens = [];
  const h = harness({ meResponses: [
    () => json(me("pending")),
    (init) => { tokens.push(init.headers.Authorization); return json({ detail: { code: "auth_unauthorized" } }, 401); },
    (init) => { tokens.push(init.headers.Authorization); return json(me("approved")); },
  ] });
  await h.login();
  assert.equal((await h.manager.refreshAccess()).access.status, "approved");
  assert.deepEqual(tokens, ["Bearer firebase-a", "Bearer firebase-refreshed"]);
});

test("access request sends only the bearer token and an empty body", async () => {
  let seen;
  const h = harness({
    meResponses: [() => json(me("unregistered"))],
    request: (init) => { seen = init; return json({ access_status: "pending", access_requested_at: "2026-10-05T01:00:00Z", created: true }); },
  });
  await h.login();
  const state = await h.manager.requestAccess();
  assert.equal(state.access.status, "pending");
  assert.equal(state.access.requestedAt, "2026-10-05T01:00:00Z");
  assert.equal(seen.method, "POST");
  assert.equal(seen.body, "{}");
  assert.equal(seen.headers.Authorization, "Bearer firebase-a");
  assert.equal(seen.redirect, "error");
});

test("a repeated request after rejection keeps the rejection", async () => {
  const h = harness({
    meResponses: [() => json(me("rejected"))],
    request: () => json({ access_status: "rejected", access_requested_at: "2026-10-05T01:00:00Z", created: false }),
  });
  await h.login();
  assert.equal((await h.manager.requestAccess()).access.status, "rejected");
});

test("environment-allowlist servers never receive access requests", async () => {
  const h = harness({ meResponses: [() => json({ uid: "user-a" })], request: () => assert.fail("no request") });
  await h.login();
  await assert.rejects(h.manager.requestAccess(), /사용 신청/);
});

test("request failures surface the server message without changing state", async () => {
  const h = harness({
    meResponses: [() => json(me("unregistered"))],
    request: () => json({ detail: { code: "access_request_quota_exceeded", message: "오늘 신청 수 초과" } }, 429),
  });
  await h.login();
  await assert.rejects(h.manager.requestAccess(), /오늘 신청 수 초과/);
  assert.equal(h.manager.state().access.status, "unregistered");
});

test("logout clears access and blocks every purpose", async () => {
  const h = harness({ meResponses: [() => json(me("approved", { can_manage_access: true }))] });
  await h.login();
  const state = await h.manager.signOut();
  assert.equal(state.access, null);
  for (const purpose of ["feature", "access", "admin"]) await assert.rejects(h.credential(purpose));
  await assert.rejects(h.manager.refreshAccess());
});

test("worker restart re-reads approval from the server instead of trusting a cache", async () => {
  const before = harness({ restored: true, meResponses: [() => json(me("approved"))] });
  const after = harness({ restored: true, meResponses: [() => json(me("blocked"))] });
  await before.manager.configure(ORIGIN);
  await after.manager.configure(ORIGIN);
  assert.equal(before.manager.state().access.status, "approved");
  assert.equal(after.manager.state().access.status, "blocked");
  assert.notEqual(before.manager.state().sessionGeneration, after.manager.state().sessionGeneration);
});

test("broadcast state never contains Firebase or Google tokens", async () => {
  const h = harness({ meResponses: [() => json(me("pending")), () => json(me("approved"))],
    request: () => json({ access_status: "pending" }) });
  await h.login();
  await h.manager.refreshAccess();
  await h.credential("feature");
  assert.doesNotMatch(JSON.stringify(h.messages), /firebase-a|firebase-refreshed|google-signin/);
  assert.doesNotMatch(JSON.stringify(h.changes), /firebase-a|firebase-refreshed|google-signin/);
});

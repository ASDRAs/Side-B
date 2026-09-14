const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const ORIGIN = "https://api.example";
const OTHER = "https://other.example";
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const flush = () => new Promise((resolve) => setImmediate(resolve));
function harness(options = {}) {
  const calls = [], messages = [], scopes = [], tokens = [];
  const user = { uid: "user-a", email: "a@example.com" };
  const auth = { currentUser: options.restored ? user : null };
  let observer;
  let token = "firebase-a";
  let exchanges = 0;
  let signouts = 0;
  const context = vm.createContext({ URL, AbortController, console, clearTimeout,
    setTimeout: (fn, ms) => setTimeout(fn, options.timeout ?? ms) });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../dist/authWorker.js"), "utf8"), context);
  const sdk = {
    initializeApp: (config) => config,
    initializeAuth: () => auth,
    indexedDBLocalPersistence: {},
    GoogleAuthProvider: { credential: (_id, access) => { assert.equal(access, "google-signin"); return {}; } },
    onIdTokenChanged: (_auth, callback) => { observer = callback; queueMicrotask(() => callback(auth.currentUser)); },
    signInWithCredential: async () => {
      exchanges++;
      if (options.exchange) await options.exchange.promise;
      auth.currentUser = user;
      observer(user);
      return { user };
    },
    signOut: async () => { signouts++; auth.currentUser = null; observer(null); },
    getIdToken: async (_user, force) => {
      tokens.push(force);
      if (options.getToken) return options.getToken(force);
      if (force) token = "firebase-refreshed";
      return token;
    },
  };
  const manager = context.SideBAuthBundle.createAuthManager({
    config: { firebase: options.blankConfig ? {} : { apiKey: "public-fixture", authDomain: "fixture.firebaseapp.com", projectId: "fixture", appId: "fixture" },
      trustedBackendOrigins: [ORIGIN, OTHER, "http://localhost:8000"], legacyServerOrigins: [ORIGIN] },
    chromeApi: {
      runtime: { sendMessage: async (message) => { messages.push(message); } },
      identity: { getAuthToken: async (details) => {
        scopes.push(details);
        if (options.google) return options.google.promise;
        return { token: "google-signin" };
      } },
    }, sdk,
    fetchImpl: async (url, init) => {
      calls.push({ url, init });
      if (options.fetch) return options.fetch(url, init);
      if (url.endsWith("/auth/config")) return new Response(JSON.stringify({ mode: options.mode || "firebase", firebase_project_id: "fixture" }), { status: options.configStatus || 200 });
      return new Response(JSON.stringify({ uid: user.uid, email: user.email }), { status: options.meStatus || 200 });
    },
  });
  return { manager, calls, messages, scopes, tokens, auth, sdk,
    exchanges: () => exchanges, signouts: () => signouts,
    credential: (extra = {}) => manager.credential({ apiBaseUrl: ORIGIN, ...extra }),
    async login() { await manager.configure(ORIGIN); return manager.signIn(); } };
}

test("managed sign-in uses only identity scopes and never returns Google access tokens", async () => {
  const h = harness();
  assert.equal((await h.login()).status, "signed_in");
  assert.deepEqual(Array.from(h.scopes[0].scopes), ["openid", "email", "profile"]);
  assert.equal((await h.credential({ legacyToken: "do-not-send" })).headers.Authorization, "Bearer firebase-a");
  assert.equal(h.calls.every(({ init }) => init.redirect === "error"), true);
  assert.doesNotMatch(JSON.stringify(h.messages), /firebase-a|google-signin|do-not-send/);
  await h.manager.signOut();
  assert.equal(h.manager.state().status, "signed_out");
  await assert.rejects(h.credential(), /Google 로그인/);
});

for (const mode of ["firebase", "dual"]) test(`${mode} signed-out state cannot fall back to a team token`, async () => {
  const h = harness({ mode });
  await h.manager.configure(ORIGIN);
  await assert.rejects(h.credential({ legacyToken: "valid-old-token" }), /Google 로그인/);
});

test("legacy is explicit and a remote server still requires its shared token", async () => {
  const h = harness({ mode: "legacy" });
  await h.manager.configure(ORIGIN);
  await assert.rejects(h.credential(), /팀 백엔드 토큰/);
  assert.equal((await h.credential({ legacyToken: "old", legacyHeader: "X-Side-B-Export-Token" })).headers["X-Side-B-Export-Token"], "old");
  await h.manager.configure("http://localhost:8000");
  assert.equal(Object.keys((await h.manager.credential({ apiBaseUrl: "http://localhost:8000" })).headers).length, 0);
});

test("only an explicitly approved old server may interpret 404 as legacy", async () => {
  const h = harness({ configStatus: 404 });
  assert.equal((await h.manager.configure(ORIGIN)).compatibility, "legacy_server");
  assert.equal((await h.manager.configure(OTHER)).status, "configuration_unavailable");
  await assert.rejects(h.manager.credential({ apiBaseUrl: OTHER, legacyToken: "old" }), /인증 설정/);
});

test("missing Firebase config, server errors and unapproved accounts fail closed", async () => {
  for (const options of [{ blankConfig: true }, { configStatus: 503 }, { meStatus: 403 }]) {
    const h = harness(options);
    if (options.meStatus) assert.equal((await h.login()).status, "denied");
    else assert.equal((await h.manager.configure(ORIGIN)).status, "configuration_unavailable");
    await assert.rejects(h.credential({ legacyToken: "old" }));
  }
});

test("untrusted origins are rejected before discovery or credential access", async () => {
  const h = harness();
  for (const url of ["https://evil.example", "https://api.example/path", "https://user:pass@api.example"]) {
    assert.throws(() => h.manager.configure(url));
    await assert.rejects(h.manager.credential({ apiBaseUrl: url }));
  }
  assert.equal(h.calls.length, 0);
});

test("logout while Google consent is pending never exchanges the late token", async () => {
  const google = deferred();
  const h = harness({ google });
  await h.manager.configure(ORIGIN);
  const pending = h.manager.signIn();
  await flush();
  await h.manager.signOut();
  google.resolve({ token: "google-signin" });
  await pending;
  assert.equal(h.exchanges(), 0);
  assert.equal(h.manager.state().status, "signed_out");
});

test("logout during the Firebase exchange clears its late persistence write", async () => {
  const exchange = deferred();
  const h = harness({ exchange });
  await h.manager.configure(ORIGIN);
  const pending = h.manager.signIn();
  await flush();
  await h.manager.signOut();
  exchange.resolve();
  await pending;
  await flush();
  assert.equal(h.auth.currentUser, null);
  assert.equal(h.manager.state().status, "signed_out");
  await assert.rejects(h.credential());
});

test("cancelled Google consent stays signed out without exchanging a credential", async () => {
  const google = deferred();
  const h = harness({ google });
  await h.manager.configure(ORIGIN);
  const pending = h.manager.signIn();
  google.reject(new Error("user rejected"));
  assert.equal((await pending).status, "signed_out");
  assert.equal(h.exchanges(), 0);
});

test("concurrent and late 401s share one forced refresh for the rejected token", async () => {
  const h = harness();
  await h.login();
  const results = await Promise.all(Array.from({ length: 5 }, () => h.credential({ forceRefresh: true, rejectedToken: "firebase-a" })));
  await h.credential({ forceRefresh: true, rejectedToken: "firebase-a" });
  assert.equal(h.tokens.filter(Boolean).length, 1);
  assert.equal(results.every((result) => result.headers.Authorization === "Bearer firebase-refreshed"), true);
});

test("logout while obtaining an ID token prevents credential delivery", async () => {
  const h = harness();
  await h.login();
  const deferredToken = deferred();
  h.sdk.getIdToken = () => deferredToken.promise;
  const pending = h.credential();
  await h.manager.signOut();
  deferredToken.resolve("late-secret");
  await assert.rejects(pending, /변경/);
});

test("server changes invalidate late configuration response bodies", async () => {
  const body = deferred();
  const h = harness({ fetch: async (url) => url.startsWith(ORIGIN)
    ? { ok: true, status: 200, json: () => body.promise }
    : new Response(JSON.stringify({ mode: "legacy" })) });
  const pending = h.manager.configure(ORIGIN);
  await flush();
  await h.manager.configure(OTHER);
  body.resolve({ mode: "firebase", firebase_project_id: "fixture" });
  await pending;
  assert.equal(h.manager.state().apiOrigin, OTHER);
  assert.equal(h.manager.state().mode, "legacy");
});

test("configuration body timeouts are bounded and never downgrade", async () => {
  const h = harness({ timeout: 15, fetch: async () => ({ ok: true, status: 200, json: () => new Promise(() => {}) }) });
  assert.equal((await h.manager.configure(ORIGIN)).status, "configuration_unavailable");
  await assert.rejects(h.credential({ legacyToken: "old" }));
});

test("worker restarts recover SDK persistence with a new account cache generation", async () => {
  const a = harness({ restored: true }), b = harness({ restored: true });
  await a.manager.configure(ORIGIN);
  await b.manager.configure(ORIGIN);
  assert.equal(b.manager.state().status, "signed_in");
  assert.notEqual(a.manager.state().sessionGeneration, b.manager.state().sessionGeneration);
});

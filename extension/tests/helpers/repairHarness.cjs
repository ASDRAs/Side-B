const vm = require("node:vm");
const { loadBackground, response } = require("./backgroundHarness.cjs");

const ORIGIN = "https://side-b-backend-1073342688292.asia-northeast3.run.app";
const settle = async (turns = 8) => { for (let i = 0; i < turns; i++) await new Promise(setImmediate); };
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const me = (uid = "A", status = "approved", canManage = true) => response(200, {
  uid, email: `${uid}@example.com`, access_status: status,
  access_store: "firestore", can_manage_access: canManage,
});

function repairHarness({ restored = false, timeout } = {}) {
  const a = { uid: "A", email: "A@example.com" };
  const b = { uid: "B", email: "B@example.com" };
  const auth = { currentUser: restored ? a : null };
  let observer, tokenImpl;
  const tokens = new Map([[a, "fixture-A"], [b, "fixture-B"]]);
  const tokenCalls = [];
  let signouts = 0;
  const sdk = {
    initializeApp: (config) => config, initializeAuth: () => auth,
    indexedDBLocalPersistence: {}, GoogleAuthProvider: { credential: () => ({}) },
    onIdTokenChanged: (_auth, callback) => { observer = callback; queueMicrotask(() => callback(auth.currentUser)); },
    signInWithCredential: async () => { auth.currentUser = a; observer(a); return { user: a }; },
    signOut: async () => { signouts++; auth.currentUser = null; observer(null); },
    getIdToken: async (user, force) => {
      tokenCalls.push({ uid: user.uid, force });
      if (tokenImpl) return tokenImpl(user, force);
      if (force) tokens.set(user, `fixture-${user.uid}-refreshed`);
      return tokens.get(user);
    },
  };
  const responses = [];
  const h = loadBackground(responses, {
    storage: { apiBaseUrl: ORIGIN, apiBaseUrlStorageVersion: 3 }, authSdk: sdk,
    setTimeout: timeout === undefined ? setTimeout : (fn) => setTimeout(fn, timeout),
  });
  const manager = vm.runInContext("authManager", h.context);
  const send = (message, sender = { url: "chrome-extension://test/sidepanel.html" }) =>
    h.context.handleMessage({ target: "background", ...message }, sender);
  return {
    ...h, manager, auth, sdk, a, b, responses, tokenCalls, send,
    signouts: () => signouts,
    setToken(fn) { tokenImpl = fn; },
    observe(user = auth.currentUser) { observer(user); },
    switchTo(user, notify = true) { auth.currentUser = user; if (notify) observer(user); },
    async login() {
      responses.push(response(200, { mode: "firebase", firebase_project_id: "gen-lang-client-0392647514" }), me());
      await send({ type: "AUTH_SIGN_IN" });
    },
    credential: (options = {}) => send({ type: "AUTH_GET_CREDENTIAL", apiBaseUrl: ORIGIN, ...options }),
  };
}

module.exports = { repairHarness, ORIGIN, settle, deferred, me, response };

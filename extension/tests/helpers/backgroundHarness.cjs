const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const BACKGROUND_SOURCE = fs.readFileSync(
  path.join(__dirname, "../..", "background.js"),
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
  const runtimeMessages = [];

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
      sendMessage: async (message) => { runtimeMessages.push(message); return { ok: true }; },
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
        async remove(keys) {
          for (const key of Array.isArray(keys) ? keys : [keys]) delete storage[key];
        },
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
    for (const file of files) vm.runInContext(fs.readFileSync(path.join(__dirname, "../..", file), "utf8"), context);
    if (options.authSdk) {
      const createAuthManager = context.SideBAuthBundle.createAuthManager;
      context.SideBAuthBundle = {
        ...context.SideBAuthBundle,
        // Keep the background's own options (its onChange hook) and swap only the SDK.
        createAuthManager: (args = {}) => createAuthManager({ ...args, sdk: options.authSdk }),
      };
    }
  };
  vm.runInContext(BACKGROUND_SOURCE, context);

  return {
    context,
    fetchCalls,
    storage,
    authCalls,
    removedTokens,
    offscreenCalls,
    runtimeMessages,
  };
}

function managedAuthSdk() {
  const auth = { currentUser: null };
  const user = { uid: "approved-user", email: "approved@example.com" };
  let observer;
  return {
    initializeApp: (config) => config,
    initializeAuth: () => auth,
    indexedDBLocalPersistence: {},
    onIdTokenChanged: (_auth, callback) => {
      observer = callback;
      queueMicrotask(() => callback(auth.currentUser));
    },
    GoogleAuthProvider: { credential: () => ({}) },
    signInWithCredential: async () => {
      auth.currentUser = user;
      observer(user);
      return { user };
    },
    signOut: async () => { auth.currentUser = null; observer(null); },
    getIdToken: async () => "fixture-firebase-token",
  };
}


module.exports = { loadBackground, response, managedAuthSdk };

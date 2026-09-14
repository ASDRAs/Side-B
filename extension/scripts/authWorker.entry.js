import { initializeApp } from "firebase/app";
export { resolveApiBaseUrlSetting } from "./apiConfig.js";
import {
  GoogleAuthProvider, getIdToken, indexedDBLocalPersistence, initializeAuth,
  onIdTokenChanged, signInWithCredential, signOut,
} from "firebase/auth/web-extension";

export const SIGN_IN_SCOPES = Object.freeze(["openid", "email", "profile"]);
const TIMEOUT_MS = 8_000;

async function bounded(promise, milliseconds = TIMEOUT_MS) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("인증 응답 시간이 초과되었습니다.")), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

export function createAuthManager({
  chromeApi = chrome,
  fetchImpl = (...args) => globalThis.fetch(...args),
  config = globalThis.SideBAuthConfig,
  sdk = { initializeApp, initializeAuth, indexedDBLocalPersistence,
    GoogleAuthProvider, signInWithCredential, signOut, onIdTokenChanged, getIdToken },
} = {}) {
  const instance = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  let sequence = 0;
  let epoch = 0;
  let observation = 0;
  let blocked = false;
  let auth = null;
  let authProject = null;
  let ready = null;
  let login = null;
  let exchange = null;
  let refresh = null;
  let configuring = null;
  let state = { status: "initializing", mode: null, apiOrigin: null,
    firebaseProjectId: null, account: null, error: null, compatibility: null,
    sessionGeneration: `${instance}:0` };
  const snapshot = () => ({ ...state, account: state.account && { ...state.account } });
  const managed = () => ["firebase", "dual"].includes(state.mode);

  function publish(patch, invalidate = false) {
    state = { ...state, ...patch };
    if (invalidate) state.sessionGeneration = `${instance}:${++sequence}`;
    for (const target of ["auth-ui", "offscreen"]) {
      Promise.resolve(chromeApi.runtime.sendMessage({
        target, type: "AUTH_STATE_CHANGED", state: snapshot(),
      })).catch(() => {});
    }
    return snapshot();
  }

  function trustedOrigin(value) {
    let url;
    try { url = new URL(value); } catch { throw new Error("백엔드 주소가 올바르지 않습니다."); }
    if (url.pathname !== "/" || url.search || url.hash || url.username || url.password ||
        !config?.trustedBackendOrigins?.includes(url.origin)) {
      throw new Error("이 백엔드에는 로그인 정보를 보낼 수 없습니다.");
    }
    return url.origin;
  }

  async function jsonRequest(url, init = {}) {
    const controller = new AbortController();
    try {
      return await bounded((async () => {
        const response = await fetchImpl(url, {
          ...init, signal: controller.signal, redirect: "error", cache: "no-store",
        });
        let payload;
        try { payload = await response.json(); } catch { payload = null; }
        return { response, payload };
      })());
    } finally { controller.abort(); }
  }

  async function validate(user, origin) {
    const token = await bounded(sdk.getIdToken(user, false));
    const { response, payload } = await jsonRequest(`${origin}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok || payload?.uid !== user.uid) {
      const error = new Error(response.status === 403
        ? "승인되지 않은 계정입니다." : "로그인 상태를 확인하지 못했습니다.");
      error.status = response.status;
      throw error;
    }
    return { uid: payload.uid, email: payload.email || user.email || null,
      displayName: payload.display_name || user.displayName || null };
  }

  async function reconcile(user) {
    if (!managed() || login || blocked) return;
    const version = ++observation;
    const revision = epoch;
    const current = () => revision === epoch && version === observation && !blocked &&
      !login && auth.currentUser === user;
    if (!user) {
      publish({ status: "signed_out", account: null, error: null }, Boolean(state.account));
      return;
    }
    try {
      const account = await validate(user, state.apiOrigin);
      if (current()) publish({ status: "signed_in", account, error: null }, state.account?.uid !== user.uid);
    } catch (error) {
      if (current()) publish({ status: error.status === 403 ? "denied" : "error",
        account: null, error: error.message }, true);
    }
  }

  async function initializeFirebase(projectId) {
    const firebase = config?.firebase || {};
    if (![firebase.apiKey, firebase.authDomain, firebase.projectId, firebase.appId].every(Boolean) ||
        firebase.projectId !== projectId) {
      throw new Error("확장 프로그램 Firebase 설정과 백엔드 프로젝트를 확인해 주세요.");
    }
    if (auth && authProject !== projectId) throw new Error("Firebase 프로젝트 변경 후 확장 프로그램을 다시 로드하세요.");
    if (!auth) {
      auth = sdk.initializeAuth(sdk.initializeApp(firebase), { persistence: sdk.indexedDBLocalPersistence });
      authProject = projectId;
      ready = new Promise((resolve) => {
        sdk.onIdTokenChanged(auth, (user) => {
          resolve();
          void reconcile(user);
        });
      });
    }
    await bounded(ready);
  }

  function configure(apiBaseUrl) {
    const origin = trustedOrigin(apiBaseUrl);
    if (configuring?.origin === origin) return configuring.promise;
    if (state.apiOrigin === origin && ["legacy", "signed_in", "signed_out"].includes(state.status)) {
      return Promise.resolve(snapshot());
    }
    const revision = ++epoch;
    publish({ status: "initializing", mode: null, apiOrigin: origin,
      account: null, error: null, compatibility: null }, state.apiOrigin !== null);
    const promise = (async () => {
      try {
        const { response, payload } = await jsonRequest(`${origin}/auth/config`);
        if (revision !== epoch) return snapshot();
        if (response.status === 404 && config.legacyServerOrigins?.includes(origin)) {
          return publish({ status: "legacy", mode: "legacy", firebaseProjectId: null,
            compatibility: "legacy_server" });
        }
        if (!response.ok || !["legacy", "dual", "firebase"].includes(payload?.mode)) {
          throw new Error("백엔드 인증 설정을 확인하지 못했습니다.");
        }
        state.mode = payload.mode;
        state.firebaseProjectId = payload.firebase_project_id || null;
        if (!managed()) return publish({ status: "legacy" });
        await initializeFirebase(state.firebaseProjectId);
        if (revision !== epoch) return snapshot();
        if (blocked) return publish({ status: "signed_out" });
        await reconcile(auth.currentUser);
        return snapshot();
      } catch (error) {
        if (revision !== epoch) return snapshot();
        return publish({ status: "configuration_unavailable", account: null, error: error.message });
      }
    })();
    configuring = { origin, promise };
    void promise.finally(() => { if (configuring?.promise === promise) configuring = null; });
    return promise;
  }

  function signIn() {
    if (login) return login;
    if (exchange) return Promise.reject(new Error("이전 로그인 처리가 끝난 뒤 다시 시도하세요."));
    if (!managed() || !auth || state.status === "configuration_unavailable") {
      return Promise.reject(new Error("Google 로그인 설정을 먼저 확인하세요."));
    }
    const revision = ++epoch;
    blocked = false;
    publish({ status: "signing_in", account: null, error: null }, true);
    // Observers never approve an interactive login independently of this operation.
    const promise = Promise.resolve().then(async () => {
      try {
        const result = await bounded(chromeApi.identity.getAuthToken({
          interactive: true, enableGranularPermissions: true, scopes: [...SIGN_IN_SCOPES],
        }), 120_000);
        if (revision !== epoch) return snapshot();
        const accessToken = typeof result === "string" ? result : result?.token;
        if (!accessToken) throw new Error("Google 로그인 토큰을 받지 못했습니다.");
        const signing = sdk.signInWithCredential(auth, sdk.GoogleAuthProvider.credential(null, accessToken));
        exchange = signing;
        // A timed-out/abandoned SDK exchange may still write persistence later.
        void signing.then(() => {
          if (revision !== epoch) return sdk.signOut(auth);
        }).finally(() => { if (exchange === signing) exchange = null; }).catch(() => {});
        const signed = await bounded(signing);
        if (revision !== epoch) return snapshot();
        const account = await validate(signed.user, state.apiOrigin);
        if (revision !== epoch) return snapshot();
        return publish({ status: "signed_in", account, error: null }, true);
      } catch (error) {
        if (revision !== epoch) return snapshot();
        ++epoch;
        blocked = true;
        await bounded(sdk.signOut(auth)).catch(() => {});
        return publish({ status: error.status === 403 ? "denied" : "signed_out",
          account: null, error: error.status === 403 ? "승인되지 않은 계정입니다." : "Google 로그인을 완료하지 못했습니다. 다시 시도하세요." }, true);
      }
    });
    login = promise;
    void promise.finally(() => { if (login === promise) login = null; });
    return promise;
  }

  async function localSignOut() {
    ++epoch;
    ++observation;
    blocked = true;
    refresh = null;
    publish({ status: state.mode === "legacy" ? "legacy" : "signed_out",
      account: null, error: null }, true);
    if (auth) await bounded(sdk.signOut(auth));
    return snapshot();
  }

  async function credential({ apiBaseUrl, legacyToken = "", legacyHeader = "X-Side-B-Access-Token",
    forceRefresh = false, rejectedToken = null } = {}) {
    const origin = trustedOrigin(apiBaseUrl);
    if (!state.apiOrigin) await configure(origin);
    if (configuring?.origin === origin) await configuring.promise;
    if (origin !== state.apiOrigin) throw new Error("백엔드 인증 설정을 다시 확인하세요.");
    const generation = state.sessionGeneration;
    const revision = epoch;
    if (managed()) {
      const user = auth?.currentUser;
      if (!user || state.status !== "signed_in" || blocked) throw new Error("Side-B에 Google 로그인이 필요합니다.");
      let token = await bounded(sdk.getIdToken(user, false));
      if (forceRefresh && (!rejectedToken || rejectedToken === token)) {
        if (!refresh) {
          const promise = bounded(sdk.getIdToken(user, true));
          refresh = promise;
          void promise.finally(() => { if (refresh === promise) refresh = null; }).catch(() => {});
        }
        token = await refresh;
      }
      if (revision !== epoch || generation !== state.sessionGeneration || user !== auth.currentUser ||
          state.status !== "signed_in" || blocked) throw new Error("로그인 계정이 변경되었습니다. 다시 요청하세요.");
      return { mode: "firebase", sessionGeneration: generation, headers: { Authorization: `Bearer ${token}` } };
    }
    if (state.mode !== "legacy" || state.status !== "legacy") throw new Error("백엔드 인증 설정을 사용할 수 없습니다.");
    if (!["X-Side-B-Access-Token", "X-Side-B-Export-Token"].includes(legacyHeader)) throw new Error("허용되지 않은 인증 헤더입니다.");
    const token = String(legacyToken || "").trim();
    if (!token && !["http://127.0.0.1:8000", "http://localhost:8000"].includes(origin)) {
      throw new Error("설정에서 팀 백엔드 토큰을 입력하세요.");
    }
    return { mode: "legacy", sessionGeneration: generation, headers: token ? { [legacyHeader]: token } : {} };
  }

  return { configure, signIn, signOut: localSignOut, credential, state: snapshot };
}

import { initializeApp } from "firebase/app";
export { resolveApiBaseUrlSetting } from "./apiConfig.js";
import {
  GoogleAuthProvider, getIdToken, indexedDBLocalPersistence, initializeAuth,
  onIdTokenChanged, signInWithCredential, signOut,
} from "firebase/auth/web-extension";

export const SIGN_IN_SCOPES = Object.freeze(["openid", "email", "profile"]);
export const ACCESS_STATUSES = Object.freeze(["unregistered", "pending", "approved", "rejected", "blocked"]);
const CREDENTIAL_PURPOSES = new Set(["feature", "access", "admin"]);
const TIMEOUT_MS = 8_000;

async function bounded(promise, milliseconds = TIMEOUT_MS) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error("인증 응답 시간이 초과되었습니다.")), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

function errorDetail(payload) {
  const detail = payload?.detail;
  return detail && typeof detail === "object" && !Array.isArray(detail) ? detail : {};
}

// The server's approval answer, separate from the Firebase session. Servers from
// before this split answer /auth/me with 200 only for allowlisted accounts.
export function accessFromPayload(payload) {
  const raw = payload?.access_status;
  const status = raw === undefined ? "approved" : ACCESS_STATUSES.includes(raw) ? raw : "unavailable";
  return {
    status,
    canManage: payload?.can_manage_access === true,
    requestedAt: typeof payload?.access_requested_at === "string" ? payload.access_requested_at : null,
    store: payload?.access_store === "firestore" ? "firestore" : "env",
    error: null,
  };
}

export function createAuthManager({
  chromeApi = chrome,
  fetchImpl = (...args) => globalThis.fetch(...args),
  config = globalThis.SideBAuthConfig,
  sdk = { initializeApp, initializeAuth, indexedDBLocalPersistence,
    GoogleAuthProvider, signInWithCredential, signOut, onIdTokenChanged, getIdToken },
  onChange = null,
} = {}) {
  const instance = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
  let sequence = 0;
  let epoch = 0;
  let accessOrder = 0;
  let observedUser;
  let validatedUser = null;
  let blocked = false;
  let auth = null;
  let authProject = null;
  let ready = null;
  let login = null;
  let exchange = null;
  let logout = null;
  let refresh = null;
  let configuring = null;
  let accessRefresh = null;
  let accessSubmit = null;
  let accessTokenCheck = null;
  let state = { status: "initializing", mode: null, apiOrigin: null,
    firebaseProjectId: null, account: null, access: null, error: null, compatibility: null,
    sessionGeneration: `${instance}:0`, stateRevision: 0 };
  const snapshot = () => ({ ...state, account: state.account && { ...state.account },
    access: state.access && { ...state.access } });
  const managed = () => ["firebase", "dual"].includes(state.mode);
  // Losing approval invalidates the session generation so in-flight feature
  // work, caches and EQ bound to the previous approval are discarded.
  const losesApproval = (next) => state.access?.status === "approved" && next?.status !== "approved";

  function publish(patch, invalidate = false) {
    const previous = snapshot();
    state = { ...state, ...patch, stateRevision: state.stateRevision + 1 };
    if (patch.account === null) validatedUser = null;
    if (invalidate) {
      state.sessionGeneration = `${instance}:${++sequence}`;
      refresh = null;
      accessRefresh = null;
      accessSubmit = null;
    }
    // Tokens never enter this state; it is safe to broadcast.
    for (const target of ["auth-ui", "offscreen"]) {
      Promise.resolve(chromeApi.runtime.sendMessage({
        target, type: "AUTH_STATE_CHANGED", state: snapshot(),
      })).catch(() => {});
    }
    try { onChange?.(snapshot(), previous); } catch { /* observers cannot break auth */ }
    return snapshot();
  }

  // The SDK observer may run after currentUser changes. Check synchronously at
  // every session boundary as well; an unvalidated account never inherits access.
  function synchronizeAccount() {
    if (!auth || !managed()) return;
    const user = auth?.currentUser || null;
    if (observedUser === user) return;
    observedUser = user;
    ++accessOrder;
    refresh = null;
    accessRefresh = null;
    accessSubmit = null;
    publish({ status: blocked ? (state.status === "denied" ? "denied" : "signed_out") :
      login ? "signing_in" : user ? "initializing" : "signed_out",
      account: null, access: null, error: blocked ? state.error : null }, true);
  }

  const captureSession = (user, origin) => ({ user, uid: user?.uid, origin,
    revision: epoch, generation: state.sessionGeneration });
  const sessionCurrent = (session) => session.revision === epoch &&
    session.generation === state.sessionGeneration && session.user === auth?.currentUser &&
    session.uid === auth?.currentUser?.uid && session.origin === state.apiOrigin && !blocked;
  const beginAccessCheck = (user, origin) => ({ ...captureSession(user, origin), order: ++accessOrder });
  const accessCurrent = (session) => sessionCurrent(session) && session.order === accessOrder;
  const accountChanged = () => new Error("로그인 계정이 변경되었습니다. 다시 요청하세요.");

  async function sessionToken(session, forceRefresh = false, rejectedToken = null) {
    if (!sessionCurrent(session)) throw accountChanged();
    let token = await bounded(sdk.getIdToken(session.user, false));
    if (!sessionCurrent(session)) throw accountChanged();
    if (forceRefresh && (!rejectedToken || rejectedToken === token)) {
      if (!refresh || refresh.user !== session.user || refresh.uid !== session.uid ||
          refresh.generation !== session.generation || refresh.revision !== session.revision ||
          refresh.origin !== session.origin) {
        const promise = bounded(sdk.getIdToken(session.user, true));
        const owner = { ...session, promise };
        refresh = owner;
        void promise.finally(() => { if (refresh === owner) refresh = null; }).catch(() => {});
      }
      token = await refresh.promise;
    }
    if (!sessionCurrent(session)) throw accountChanged();
    return token;
  }

  async function accessToken(session, forceRefresh = false, rejectedToken = null) {
    const owner = { session };
    accessTokenCheck = owner;
    try {
      const token = await sessionToken(session, forceRefresh, rejectedToken);
      if (!accessCurrent(session)) throw accountChanged();
      return token;
    } finally { if (accessTokenCheck === owner) accessTokenCheck = null; }
  }

  function signOutFirebase() {
    if (!logout) {
      // A caller timeout does not cancel the SDK's persistence write. Retain
      // ownership until the original operation settles, and bound only waits.
      const promise = sdk.signOut(auth);
      logout = promise;
      void promise.finally(() => { if (logout === promise) logout = null; }).catch(() => {});
    }
    return bounded(logout);
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

  async function validate(user, origin, forceRefresh = false, rejectedToken = null, accessSession = null) {
    const token = await (accessSession ? accessToken(accessSession, forceRefresh, rejectedToken)
      : sessionToken(captureSession(user, origin), forceRefresh, rejectedToken));
    const { response, payload } = await jsonRequest(`${origin}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.ok && payload?.uid === user.uid) {
      return {
        account: { uid: payload.uid, email: payload.email || user.email || null,
          displayName: payload.display_name || user.displayName || null },
        access: accessFromPayload(payload),
      };
    }
    const detail = errorDetail(payload);
    if (response.status === 503 && detail.code === "access_store_unavailable") {
      // Identity passed; only the approval lookup failed. Keep the session and
      // fail features closed until the status can be confirmed.
      return {
        account: { uid: user.uid, email: user.email || null, displayName: user.displayName || null },
        access: { status: "unavailable", canManage: false, requestedAt: null, store: "firestore",
          error: "계정 승인 상태를 일시적으로 확인할 수 없습니다." },
      };
    }
    let message = "로그인 상태를 확인하지 못했습니다.";
    if (response.status === 403) {
      // Diagnostics show which account and server were checked, never a token.
      const reason = detail.code === "auth_identity_unverified"
        ? "인증된 Google 계정만 사용할 수 있습니다." : "승인되지 않은 계정입니다.";
      message = `${reason} 인증 계정: ${user.email || "이메일 확인 불가"}. 요청 서버: ${origin}.` +
        (typeof detail.code === "string" ? ` (${detail.code})` : "");
    } else if (response.status === 429) {
      message = "요청이 너무 많습니다. 잠시 후 다시 확인하세요.";
    }
    const error = new Error(message);
    error.status = response.status;
    error.code = detail.code;
    error.rejectedToken = token; // private, only used for same-account 401 dedup
    throw error;
  }

  async function reconcile(user) {
    if (auth?.currentUser !== user) return;
    synchronizeAccount();
    if (!managed() || login || blocked) return;
    // getIdToken itself notifies this same User's observer. The explicit access
    // operation owns token acquisition and will query the server with that token.
    // After acquisition, newer observers still invalidate older HTTP responses.
    if (accessTokenCheck && accessCurrent(accessTokenCheck.session)) return;
    const session = beginAccessCheck(user, state.apiOrigin);
    const current = () => accessCurrent(session) && !login;
    if (!user) {
      publish({ status: "signed_out", account: null, access: null, error: null }, Boolean(state.account));
      return;
    }
    try {
      const { account, access } = await validate(user, state.apiOrigin);
      if (current()) {
        validatedUser = user;
        publish({ status: "signed_in", account, access, error: null },
          state.account?.uid !== user.uid || losesApproval(access));
      }
    } catch (error) {
      if (!current()) return;
      if (error.status !== 401 && error.status !== 403 && state.status === "signed_in" &&
          validatedUser === user && state.account?.uid === user.uid) {
        // Keep this identity and the latest access decision on transient errors.
        // In particular, do not restore approval from before an explicit denial.
        publish({ access: { ...state.access, error: error.message } });
      } else publish({ status: error.status === 403 ? "denied" : "error",
        account: null, access: null, error: error.message }, true);
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
    if (managed()) synchronizeAccount();
    if (configuring?.origin === origin) return configuring.promise;
    if (state.apiOrigin === origin && ["legacy", "signed_in", "signed_out"].includes(state.status)) {
      return Promise.resolve(snapshot());
    }
    const revision = ++epoch;
    ++accessOrder;
    publish({ status: "initializing", mode: null, apiOrigin: origin,
      account: null, access: null, error: null, compatibility: null }, state.apiOrigin !== null);
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
        return publish({ status: "configuration_unavailable", account: null, access: null, error: error.message });
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
    ++accessOrder;
    blocked = false;
    publish({ status: "signing_in", account: null, access: null, error: null }, true);
    // Observers never approve an interactive login independently of this operation.
    const promise = Promise.resolve().then(async () => {
      let validationSession;
      try {
        if (logout) await bounded(logout);
        if (revision !== epoch) return snapshot();
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
          if (revision !== epoch) return signOutFirebase();
        }).finally(() => { if (exchange === signing) exchange = null; }).catch(() => {});
        const signed = await bounded(signing);
        if (revision !== epoch) return snapshot();
        synchronizeAccount();
        validationSession = beginAccessCheck(signed.user, state.apiOrigin);
        // A pending, rejected or blocked account is still a valid login. Only an
        // identity rejection (403) ends the Firebase session.
        const { account, access } = await validate(signed.user, state.apiOrigin);
        if (!accessCurrent(validationSession)) return snapshot();
        validatedUser = signed.user;
        return publish({ status: "signed_in", account, access, error: null }, true);
      } catch (error) {
        if (revision !== epoch || (validationSession && !accessCurrent(validationSession))) return snapshot();
        ++epoch;
        blocked = true;
        publish({ status: error.status === 403 ? "denied" : "signed_out",
          account: null, access: null,
          error: error.status === 403 ? error.message : "Google 로그인을 완료하지 못했습니다. 다시 시도하세요." }, true);
        await signOutFirebase().catch(() => {});
        return snapshot();
      }
    });
    login = promise;
    void promise.finally(() => {
      if (login === promise) {
        login = null;
        if (state.status === "signing_in" && !blocked) void reconcile(auth.currentUser);
      }
    });
    return promise;
  }

  function requireSession() {
    synchronizeAccount();
    if (!managed() || !auth || state.status !== "signed_in" || blocked || !auth.currentUser) {
      throw new Error("Side-B에 Google 로그인이 필요합니다.");
    }
    if (validatedUser !== auth.currentUser || state.account?.uid !== auth.currentUser.uid) throw accountChanged();
    return captureSession(auth.currentUser, state.apiOrigin);
  }

  function refreshAccess() {
    let session;
    try { session = requireSession(); } catch (error) { return Promise.reject(error); }
    if (accessRefresh && accessCurrent(accessRefresh.session)) return accessRefresh.promise;
    session = beginAccessCheck(session.user, session.origin);
    const current = () => accessCurrent(session) && state.status === "signed_in";
    const promise = (async () => {
      let result;
      try {
        try { result = await validate(session.user, session.origin, false, null, session); }
        catch (error) {
          if (error.status !== 401 || !current()) throw error;
          result = await validate(session.user, session.origin, true, error.rejectedToken, session);
        }
      } catch (error) {
        if (!current()) return snapshot();
        if (error.status === 403) {
          // Identity rejection, not an approval decision: end the session.
          ++epoch;
          blocked = true;
          publish({ status: "denied", account: null, access: null, error: error.message }, true);
          await signOutFirebase().catch(() => {});
          return snapshot();
        }
        if (error.status === 401) {
          return publish({ status: "error", account: null, access: null, error: error.message }, true);
        }
        return publish({ access: { ...(state.access || {}), error: error.message } });
      }
      if (!current()) return snapshot();
      validatedUser = session.user;
      return publish({ account: result.account, access: result.access, error: null },
        losesApproval(result.access) || state.account?.uid !== result.account.uid);
    })();
    const owner = { session, promise };
    accessRefresh = owner;
    void promise.finally(() => { if (accessRefresh === owner) accessRefresh = null; }).catch(() => {});
    return promise;
  }

  function requestAccess() {
    let session;
    try { session = requireSession(); } catch (error) { return Promise.reject(error); }
    if (state.access?.store !== "firestore") {
      return Promise.reject(new Error("이 서버는 사용 신청을 받지 않습니다."));
    }
    if (accessSubmit && accessCurrent(accessSubmit.session)) return accessSubmit.promise;
    session = beginAccessCheck(session.user, session.origin);
    const current = () => accessCurrent(session) && state.status === "signed_in";
    const promise = (async () => {
      const token = await accessToken(session);
      if (!current()) return snapshot();
      // The body is empty on purpose: the server takes UID and email from the token.
      const { response, payload } = await jsonRequest(`${session.origin}/access/request`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: "{}",
      });
      if (!current()) return snapshot();
      if (!response.ok) {
        const detail = errorDetail(payload);
        throw new Error(typeof detail.message === "string" && detail.message
          ? detail.message : `사용 신청에 실패했습니다. (HTTP ${response.status})`);
      }
      const status = ACCESS_STATUSES.includes(payload?.access_status) ? payload.access_status : "unavailable";
      const access = { ...(state.access || {}), status, error: null,
        requestedAt: typeof payload?.access_requested_at === "string" ? payload.access_requested_at : null };
      return publish({ access }, losesApproval(access));
    })();
    const owner = { session, promise };
    accessSubmit = owner;
    void promise.finally(() => { if (accessSubmit === owner) accessSubmit = null; }).catch(() => {});
    return promise;
  }

  async function localSignOut() {
    ++epoch;
    ++accessOrder;
    blocked = true;
    refresh = null;
    accessRefresh = null;
    accessSubmit = null;
    publish({ status: state.mode === "legacy" ? "legacy" : "signed_out",
      account: null, access: null, error: null }, true);
    if (auth) await signOutFirebase();
    return snapshot();
  }

  function purposeAllowed(purpose) {
    if (purpose === "access") return true;
    if (purpose === "admin") return state.access?.canManage === true;
    return state.access?.status === "approved";
  }

  function purposeError(purpose) {
    const error = new Error(purpose === "admin"
      ? "관리자 권한이 없는 계정입니다." : "관리자 승인 후 사용할 수 있습니다.");
    error.code = purpose === "admin" ? "admin_required" : "access_not_approved";
    return error;
  }

  function reportDenial({ purpose, code, accessStatus, apiOrigin, sessionGeneration } = {}) {
    if (!((purpose === "feature" && code === "access_not_approved") ||
        (purpose === "admin" && code === "admin_required"))) {
      throw new Error("허용되지 않은 권한 거부 보고입니다.");
    }
    const origin = trustedOrigin(apiOrigin);
    if (apiOrigin !== origin) throw new Error("백엔드 origin이 올바르지 않습니다.");
    synchronizeAccount();
    if (!managed() || state.status !== "signed_in" || blocked ||
        validatedUser !== auth?.currentUser || !state.account || state.account.uid !== auth?.currentUser?.uid ||
        sessionGeneration !== state.sessionGeneration || origin !== state.apiOrigin) {
      return { applied: false, state: snapshot() };
    }
    ++accessOrder; // invalidate all queries begun before this explicit denial
    accessRefresh = null;
    accessSubmit = null;
    const access = { ...state.access, error: purposeError(purpose).message };
    if (purpose === "admin") access.canManage = false;
    else access.status = ACCESS_STATUSES.includes(accessStatus) && accessStatus !== "approved"
      ? accessStatus : "unavailable";
    return { applied: true, state: publish({ access }, purpose === "feature") };
  }

  async function credential({ apiBaseUrl, legacyToken = "", legacyHeader = "X-Side-B-Access-Token",
    forceRefresh = false, rejectedToken = null, purpose = "feature" } = {}) {
    if (!CREDENTIAL_PURPOSES.has(purpose)) throw new Error("허용되지 않은 인증 용도입니다.");
    const origin = trustedOrigin(apiBaseUrl);
    if (!state.apiOrigin) await configure(origin);
    if (configuring?.origin === origin) await configuring.promise;
    if (origin !== state.apiOrigin) throw new Error("백엔드 인증 설정을 다시 확인하세요.");
    if (managed()) {
      const session = requireSession();
      // Unapproved sessions may still check or request access; the server makes
      // the final decision for every call regardless of this local gate.
      if (!purposeAllowed(purpose)) throw purposeError(purpose);
      const token = await sessionToken(session, forceRefresh, rejectedToken);
      if (!sessionCurrent(session) || validatedUser !== session.user || state.account?.uid !== session.uid ||
          state.status !== "signed_in" || !purposeAllowed(purpose)) {
        throw accountChanged();
      }
      return { mode: "firebase", apiOrigin: origin, sessionGeneration: session.generation,
        headers: { Authorization: `Bearer ${token}` } };
    }
    if (state.mode !== "legacy" || state.status !== "legacy") throw new Error("백엔드 인증 설정을 사용할 수 없습니다.");
    if (purpose === "admin") throw purposeError(purpose);
    if (!["X-Side-B-Access-Token", "X-Side-B-Export-Token"].includes(legacyHeader)) throw new Error("허용되지 않은 인증 헤더입니다.");
    const token = String(legacyToken || "").trim();
    if (!token && !["http://127.0.0.1:8000", "http://localhost:8000"].includes(origin)) {
      throw new Error("설정에서 팀 백엔드 토큰을 입력하세요.");
    }
    return { mode: "legacy", apiOrigin: origin, sessionGeneration: state.sessionGeneration,
      headers: token ? { [legacyHeader]: token } : {} };
  }

  return { configure, signIn, signOut: localSignOut, credential, refreshAccess, requestAccess, reportDenial, state: snapshot };
}

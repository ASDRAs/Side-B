export async function authMessage(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({
    target: "background",
    type,
    ...payload,
  });
  if (!response?.ok) throw new Error(response?.error || "로그인 요청에 실패했습니다.");
  return response;
}

export async function configureAuth(apiBaseUrl) {
  return (await authMessage("AUTH_CONFIGURE", { apiBaseUrl })).state;
}

export async function signIn() {
  return (await authMessage("AUTH_SIGN_IN")).state;
}

export async function signOut() {
  return (await authMessage("AUTH_SIGN_OUT")).state;
}

// Re-reads the server's approval state for the signed-in account.
export async function refreshAccess() {
  return (await authMessage("AUTH_REFRESH_ACCESS")).state;
}

// Asks the server to record the signed-in account's own access request.
export async function requestAccess() {
  return (await authMessage("AUTH_REQUEST_ACCESS")).state;
}

export async function backendCredential(
  apiBaseUrl,
  legacyToken,
  forceRefresh = false,
  legacyHeader = "X-Side-B-Access-Token",
  rejectedToken = null,
  purpose = "feature",
) {
  return (await authMessage("AUTH_GET_CREDENTIAL", {
    apiBaseUrl,
    legacyToken,
    forceRefresh,
    legacyHeader,
    rejectedToken,
    purpose,
  })).credential;
}

export async function authenticatedFetch(
  fetchImpl,
  url,
  init,
  { apiBaseUrl, legacyToken = "", legacyHeader = "X-Side-B-Access-Token", purpose = "feature" },
) {
  async function cancellable(promise) {
    const signal = init?.signal;
    if (!signal) return promise;
    let onAbort;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        onAbort = () => reject(signal.reason);
        signal.addEventListener("abort", onAbort, { once: true });
        if (signal.aborted) onAbort();
      })]);
    } finally { signal.removeEventListener("abort", onAbort); }
  }
  const target = new URL(url);
  if (target.origin !== new URL(apiBaseUrl).origin || target.username || target.password) {
    throw new Error("다른 서버에는 로그인 정보를 보낼 수 없습니다.");
  }
  const request = async (previous = null) => {
    init?.signal?.throwIfAborted();
    const credential = await cancellable(backendCredential(
      apiBaseUrl,
      legacyToken,
      Boolean(previous),
      legacyHeader,
      previous?.headers?.Authorization?.replace(/^Bearer /, ""),
      purpose,
    ));
    init?.signal?.throwIfAborted();
    if (previous && credential.sessionGeneration !== previous.sessionGeneration) {
      throw new Error("로그인 계정이 변경되었습니다. 다시 요청하세요.");
    }
    const headers = new Headers(init?.headers);
    for (const name of ["Authorization", "X-Side-B-Access-Token", "X-Side-B-Export-Token"]) headers.delete(name);
    for (const [name, value] of Object.entries(credential.headers)) headers.set(name, value);
    const response = await fetchImpl(url, {
      ...init,
      headers,
      redirect: "error",
      cache: "no-store",
    });
    return { response, credential };
  };
  const first = await request();
  let result = first;
  if (first.response.status === 401 && first.credential.mode === "firebase") {
    result = await request(first.credential);
  }
  if (result.response.status === 403 && result.credential.mode === "firebase") {
    let payload;
    try { payload = await result.response.clone().json(); } catch { payload = null; }
    const detail = payload?.detail;
    if ((purpose === "feature" && detail?.code === "access_not_approved") ||
        (purpose === "admin" && detail?.code === "admin_required")) {
      // Carry the exact credential scope; a late response cannot revoke a new
      // account, approval generation, backend or worker instance.
      await authMessage("AUTH_REPORT_DENIAL", {
        purpose, code: detail.code, accessStatus: detail.access_status,
        apiOrigin: target.origin, sessionGeneration: result.credential.sessionGeneration,
      });
    }
  }
  return result.response;
}

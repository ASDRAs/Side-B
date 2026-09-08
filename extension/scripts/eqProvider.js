globalThis.SideBEqProvider = (() => {
  let configure;
  const configuration = new Promise((resolve) => { configure = resolve; });
  // 설정 모듈은 문서 파싱 직후 실행된다. 몇 초 안에 오지 않으면 로딩이 실패한
  // 것이므로 170초 분석 타임아웃과 구분해 즉시 알리고, 대기 중 취소도 받는다.
  const CONFIGURE_TIMEOUT_MS = 5_000;

  async function configured(signal) {
    signal.throwIfAborted();
    let timer;
    let onAbort;
    try {
      return await Promise.race([
        configuration,
        new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error("EQ 설정을 불러오지 못했습니다. 확장을 다시 로드해 주세요.")), CONFIGURE_TIMEOUT_MS);
          onAbort = () => reject(signal.reason);
          signal.addEventListener("abort", onAbort, { once: true });
        }),
      ]);
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }

  // 캐시 수명은 EQ 세션 하나다. 명시적인 EQ 시작과 모드 변경은 resetCache로
  // 429 대기 중이 아니라면 백엔드를 다시 확인한다. 토큰 만료나 백엔드 장애가 캐시 뒤에
  // 숨지 않는다. 자동 곡 전환만 캐시를 쓴다.
  const CACHE_MAX_ENTRIES = 200;
  const genres = new Map();
  let cacheScope = "";
  let retryUntil = 0;

  function retryDelay(value) {
    const text = String(value ?? "").trim();
    const milliseconds = /^\d+$/.test(text) ? Number(text) * 1000 : Date.parse(text) - Date.now();
    // Bound malformed or excessive server delays; never keep a sleeping task alive.
    return Number.isFinite(milliseconds) && milliseconds > 0
      ? Math.min(milliseconds, 300_000) : 60_000;
  }

  function resetCache() {
    genres.clear();
  }

  function presetFor(genre) {
    const preset = SideBEqPresets.forGenre(genre);
    return preset ? { ...preset, genre } : null;
  }

  async function getPreset(track, { signal }) {
    const title = String(track?.title || "").trim();
    const artist = String(track?.artist || "").trim();
    if (!title || !artist) throw new Error("곡명과 아티스트를 확인할 수 없습니다.");
    if (title.length > 200 || artist.length > 200) throw new Error("곡 정보가 너무 깁니다.");
    const api = await configured(signal);
    signal.throwIfAborted();
    // Offscreen only has chrome.runtime. Read settings through the worker, but
    // own the long fetch here so worker suspension cannot kill the request.
    const result = await chrome.runtime.sendMessage({ target: "background", type: "GET_EQ_SETTINGS" });
    signal.throwIfAborted();
    if (!result?.ok) throw new Error("백엔드 설정을 읽지 못했습니다.");
    const stored = result.settings || {};
    const { apiBaseUrl } = api.resolveApiBaseUrlSetting(stored.apiBaseUrl, stored.apiBaseUrlStorageVersion);
    let url;
    try { url = new URL(apiBaseUrl); } catch { throw new Error("백엔드 주소가 올바르지 않습니다."); }
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if ((url.protocol !== "https:" && !(local && url.protocol === "http:")) ||
        url.username || url.password || url.search || url.hash) {
      throw new Error("백엔드 주소는 HTTPS 또는 로컬 HTTP 주소여야 합니다.");
    }
    const token = String(stored.backendAccessToken || "").trim();
    if (api.requiresBackendAccessToken(apiBaseUrl) && !token) throw new Error("설정에서 팀 백엔드 토큰을 입력하세요.");
    // 백엔드 주소가 바뀌면 이전 결과는 다른 서버의 것이다.
    if (cacheScope !== apiBaseUrl) { resetCache(); cacheScope = apiBaseUrl; retryUntil = 0; }
    const key = SideBEqPresets.trackKey(track);
    if (key && genres.has(key)) return presetFor(genres.get(key));
    if (Date.now() < retryUntil) {
      throw new Error(api.backendErrorMessage(429, "", String(Math.ceil((retryUntil - Date.now()) / 1000))));
    }
    const response = await fetch(`${apiBaseUrl}/genre-classification`, {
      method: "POST", headers: api.recommendationHeaders(token),
      body: JSON.stringify({ track_name: title, artist }), signal, redirect: "error",
    });
    let payload;
    try { payload = await response.json(); } catch { payload = null; }
    signal.throwIfAborted();
    if (response.status === 429 && cacheScope === apiBaseUrl) {
      retryUntil = Math.max(retryUntil, Date.now() + retryDelay(response.headers.get("Retry-After")));
    }
    if (!response.ok) throw new Error(api.backendErrorMessage(response.status,
      api.apiErrorMessage(payload, ""), response.headers.get("Retry-After")));
    if (typeof payload?.genre !== "string" || !Number.isFinite(payload.score) ||
        typeof payload.model_version !== "string" || !payload.model_version.trim()) {
      throw new Error("장르 분석 응답 형식이 올바르지 않습니다.");
    }
    // score is an SVM margin, not confidence. Negative margins are valid.
    if (key) {
      // Map은 삽입 순서를 지키므로 첫 키가 가장 오래된 항목이다.
      if (genres.size >= CACHE_MAX_ENTRIES) genres.delete(genres.keys().next().value);
      genres.set(key, payload.genre);
    }
    return presetFor(payload.genre);
  }

  return { configure, getPreset, resetCache };
})();

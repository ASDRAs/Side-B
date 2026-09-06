globalThis.SideBEqProvider = (() => {
  let configure;
  const configuration = new Promise((resolve) => { configure = resolve; });

  async function getPreset(track, { signal }) {
    const title = String(track?.title || "").trim();
    const artist = String(track?.artist || "").trim();
    if (!title || !artist) throw new Error("곡명과 아티스트를 확인할 수 없습니다.");
    if (title.length > 200 || artist.length > 200) throw new Error("곡 정보가 너무 깁니다.");
    const api = await configuration;
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
    const response = await fetch(`${apiBaseUrl}/genre-classification`, {
      method: "POST", headers: api.recommendationHeaders(token),
      body: JSON.stringify({ track_name: title, artist }), signal, redirect: "error",
    });
    let payload;
    try { payload = await response.json(); } catch { payload = null; }
    signal.throwIfAborted();
    if (!response.ok) throw new Error(api.backendErrorMessage(response.status,
      api.apiErrorMessage(payload, ""), response.headers.get("Retry-After")));
    if (typeof payload?.genre !== "string" || !Number.isFinite(payload.score) ||
        typeof payload.model_version !== "string" || !payload.model_version.trim()) {
      throw new Error("장르 분석 응답 형식이 올바르지 않습니다.");
    }
    // score is an SVM margin, not confidence. Negative margins are valid.
    const preset = SideBEqPresets.forGenre(payload.genre);
    return preset ? { ...preset, genre: payload.genre } : null;
  }

  return { configure, getPreset };
})();

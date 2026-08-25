export const DEFAULT_API_BASE_URL =
  "https://side-b-backend-7hmhv6htsa-du.a.run.app";
export const API_BASE_URL_STORAGE_VERSION = 1;

const LEGACY_DEFAULT_API_BASE_URLS = new Set([
  "http://127.0.0.1:8000",
]);
const LOCAL_API_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);

function trimTrailingSlashes(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

export function resolveApiBaseUrlSetting(storedUrl, storedVersion) {
  const apiBaseUrl = trimTrailingSlashes(storedUrl);
  const version = Number(storedVersion);
  const isCurrentVersion = version === API_BASE_URL_STORAGE_VERSION;

  if (!apiBaseUrl || (!isCurrentVersion && LEGACY_DEFAULT_API_BASE_URLS.has(apiBaseUrl))) {
    return {
      apiBaseUrl: DEFAULT_API_BASE_URL,
      shouldPersist: true,
    };
  }

  return {
    apiBaseUrl,
    shouldPersist: !isCurrentVersion,
  };
}

export function requiresBackendAccessToken(apiBaseUrl) {
  return !LOCAL_API_HOSTS.has(new URL(apiBaseUrl).hostname);
}

// preview가 ID로 조회할 수 있는 공급자. 백엔드 `_lookup_media`는 이 둘만
// 처리하고 나머지는 None을 반환해 404가 된다. source_id에는 `lastfm:`도 올 수
// 있으므로(models.py `_SOURCE_ID_PRIORITY`) 여기서 걸러 곡명 검색으로 보낸다.
const PREVIEW_ID_PROVIDERS = new Set(["itunes", "deezer"]);

// source_id는 "itunes:1533894681"처럼 provider와 track id를 콜론으로 잇는다.
// 백엔드는 반쪽만 온 ID를 거절하므로(preview.py `_require_target`) 둘 다 있을
// 때만 ID 경로를 쓰고, 없으면 곡명 검색으로 넘긴다.
export function previewQueryParams({ source_id, track_name, artist } = {}) {
  const sourceId = String(source_id || "").trim();
  const separator = sourceId.indexOf(":");
  if (
    separator > 0 &&
    separator < sourceId.length - 1 &&
    PREVIEW_ID_PROVIDERS.has(sourceId.slice(0, separator))
  ) {
    return new URLSearchParams({
      provider: sourceId.slice(0, separator),
      provider_track_id: sourceId.slice(separator + 1),
    });
  }

  const track = String(track_name || "").trim();
  if (!track) {
    return null;
  }
  const params = new URLSearchParams({ track });
  const trackArtist = String(artist || "").trim();
  if (trackArtist) {
    params.set("artist", trackArtist);
  }
  return params;
}

function retryAfterSuffix(retryAfter) {
  const seconds = Number(String(retryAfter || "").trim());
  return Number.isFinite(seconds) && seconds > 0
    ? `${Math.ceil(seconds)}초 후 다시 시도하세요.`
    : "잠시 후 다시 시도하세요.";
}

// 백엔드는 detail.message로 정확한 진단을 보낸다(recommend.py). 그 문장을
// 버리고 status만으로 문구를 지어내면, 운영자만 고칠 수 있는 설정 오류가
// "잠시 후 다시 시도하세요"로 덮여 영원히 해결되지 않는다.
export function backendErrorMessage(status, detail, retryAfter) {
  const message = String(detail || "").trim();

  if (status === 401 || status === 403) {
    return `${message || "팀 백엔드 토큰이 올바르지 않습니다."} 설정에서 확인하세요.`;
  }
  if (status === 429) {
    return `${message || "요청이 너무 많습니다."} ${retryAfterSuffix(retryAfter)}`;
  }
  if (message) {
    return message;
  }
  if (status >= 500) {
    return "백엔드에 문제가 있습니다. 잠시 후 다시 시도하세요.";
  }
  return `요청이 거부되었습니다. (HTTP ${status})`;
}

export function recommendationHeaders(accessToken) {
  const headers = { "Content-Type": "application/json" };
  const token = String(accessToken || "").trim();
  if (token) {
    headers["X-Side-B-Access-Token"] = token;
  }
  return headers;
}

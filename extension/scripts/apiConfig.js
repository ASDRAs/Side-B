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

export function recommendationHeaders(accessToken) {
  const headers = { "Content-Type": "application/json" };
  const token = String(accessToken || "").trim();
  if (token) {
    headers["X-Side-B-Access-Token"] = token;
  }
  return headers;
}

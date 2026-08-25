const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "apiConfig.js"),
    "utf8",
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

test("migrates the legacy localhost default to the deployed backend", async () => {
  const { DEFAULT_API_BASE_URL, resolveApiBaseUrlSetting } = await loadModule();

  assert.deepEqual(resolveApiBaseUrlSetting("http://127.0.0.1:8000", null), {
    apiBaseUrl: DEFAULT_API_BASE_URL,
    shouldPersist: true,
  });
});

test("preserves an explicitly versioned localhost override", async () => {
  const { resolveApiBaseUrlSetting } = await loadModule();

  assert.deepEqual(resolveApiBaseUrlSetting("http://127.0.0.1:8000", 1), {
    apiBaseUrl: "http://127.0.0.1:8000",
    shouldPersist: false,
  });
});

test("requires a team token only for non-local backends", async () => {
  const { requiresBackendAccessToken } = await loadModule();

  assert.equal(requiresBackendAccessToken("https://api.example.com"), true);
  assert.equal(requiresBackendAccessToken("http://127.0.0.1:8000"), false);
  assert.equal(requiresBackendAccessToken("http://localhost:8000"), false);
});

test("adds the access token header only when a token exists", async () => {
  const { recommendationHeaders } = await loadModule();

  assert.deepEqual(recommendationHeaders(" team-token "), {
    "Content-Type": "application/json",
    "X-Side-B-Access-Token": "team-token",
  });
  assert.deepEqual(recommendationHeaders(""), {
    "Content-Type": "application/json",
  });
});

test("maps backend failures to actionable messages", async () => {
  const { backendErrorMessage } = await loadModule();

  assert.match(backendErrorMessage(401, ""), /팀 백엔드 토큰/);
  assert.match(backendErrorMessage(401, ""), /설정에서 확인/);
  assert.match(backendErrorMessage(403, ""), /팀 백엔드 토큰/);
  assert.match(backendErrorMessage(429, ""), /잠시 후/);
  assert.match(backendErrorMessage(500, ""), /백엔드에 문제/);
  assert.match(backendErrorMessage(422, "query too long"), /query too long/);
});

test("keeps the backend diagnosis instead of inventing one", async () => {
  const { backendErrorMessage } = await loadModule();

  // 운영자만 고칠 수 있는 설정 오류. "잠시 후 다시 시도"로 덮으면 안 된다.
  assert.equal(
    backendErrorMessage(503, "백엔드에 SIDE_B_ACCESS_TOKEN이 설정되지 않았습니다."),
    "백엔드에 SIDE_B_ACCESS_TOKEN이 설정되지 않았습니다.",
  );
});

test("reports the retry delay the backend sent", async () => {
  const { backendErrorMessage } = await loadModule();

  assert.match(backendErrorMessage(429, "요청이 너무 많습니다.", "42"), /42초 후/);
  assert.match(backendErrorMessage(429, "", "0"), /잠시 후/);
  assert.match(backendErrorMessage(429, "", "Wed, 21 Oct 2026 07:28:00 GMT"), /잠시 후/);
});

test("prefers the source_id pair for preview lookups", async () => {
  const { previewQueryParams } = await loadModule();

  const params = previewQueryParams({
    source_id: "itunes:1533894681",
    track_name: "Lovesick Girls",
    artist: "BLACKPINK",
  });

  assert.equal(params.get("provider"), "itunes");
  assert.equal(params.get("provider_track_id"), "1533894681");
  assert.equal(params.get("track"), null);
});

test("falls back to a name search when source_id is unusable", async () => {
  const { previewQueryParams } = await loadModule();

  for (const sourceId of [null, "", "itunes", "itunes:", ":1533894681"]) {
    const params = previewQueryParams({
      source_id: sourceId,
      track_name: "혜성",
      artist: "윤하",
    });

    assert.equal(params.get("track"), "혜성", `source_id=${sourceId}`);
    assert.equal(params.get("artist"), "윤하");
    assert.equal(params.get("provider"), null);
  }
});

test("returns no preview params without a track name", async () => {
  const { previewQueryParams } = await loadModule();

  assert.equal(previewQueryParams({}), null);
  assert.equal(previewQueryParams({ artist: "윤하" }), null);
  assert.equal(previewQueryParams(), null);
});

test("sends unsupported preview providers through the name search", async () => {
  const { previewQueryParams } = await loadModule();

  // preview의 _lookup_media는 itunes/deezer만 조회한다. lastfm을 ID 경로로
  // 보내면 곡명 폴백도 건너뛴 채 404가 된다.
  const params = previewQueryParams({
    source_id: "lastfm:under-caffeine",
    track_name: "혜성",
    artist: "윤하",
  });

  assert.equal(params.get("provider"), null);
  assert.equal(params.get("track"), "혜성");
  assert.equal(params.get("artist"), "윤하");
});

test("keeps the ID path for providers preview supports", async () => {
  const { previewQueryParams } = await loadModule();

  for (const provider of ["itunes", "deezer"]) {
    const params = previewQueryParams({ source_id: `${provider}:123`, track_name: "t" });
    assert.equal(params.get("provider"), provider, provider);
    assert.equal(params.get("provider_track_id"), "123");
  }
});

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

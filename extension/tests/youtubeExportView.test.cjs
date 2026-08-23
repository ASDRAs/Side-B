const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "youtubeExportView.js"),
    "utf8",
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

test("partitions invalid recommendation tracks before matching", async () => {
  const { partitionExportableTracks } = await loadModule();

  const result = partitionExportableTracks([
    { name: " Hello ", artist: " Adele " },
    { name: "Missing Artist", artist: "" },
    { name: "", artist: "Missing Title" },
    { name: "x".repeat(201), artist: "Too Long" },
  ]);

  assert.deepEqual(result, {
    valid: [{ name: "Hello", artist: "Adele" }],
    invalid: 3,
    requested: 4,
  });
});

test("orders matched and unmatched review rows by backend position", async () => {
  const { orderedMatchReviewRows } = await loadModule();

  const rows = orderedMatchReviewRows({
    matched: [
      { name: "First", position: 0 },
      { name: "Third", position: 2 },
    ],
    unmatched: [{ name: "Second", position: 1 }],
  });

  assert.deepEqual(
    rows.map(({ kind, index, track }) => [kind, index, track.name]),
    [
      ["matched", 0, "First"],
      ["unmatched", null, "Second"],
      ["matched", 1, "Third"],
    ],
  );
});

test("leaves low-confidence candidates unchecked for manual review", async () => {
  const { shouldAutoSelectMatch } = await loadModule();

  assert.equal(shouldAutoSelectMatch({ auto_selected: false }), false);
  assert.equal(shouldAutoSelectMatch({ auto_selected: true }), true);
  assert.equal(shouldAutoSelectMatch({}), true);
});

test("matches stored state to the current operation only", async () => {
  const { isStateForOperation } = await loadModule();

  assert.equal(isStateForOperation({ operationId: "current" }, "current"), true);
  assert.equal(isStateForOperation({ operationId: "old" }, "current"), false);
  assert.equal(isStateForOperation({ status: "error" }, "current"), false);
});

test("formats failed playlist items with their identity and cause", async () => {
  const { failedTrackLabel } = await loadModule();

  assert.equal(
    failedTrackLabel({
      artist: "Adele",
      name: "Hello",
      error: "Video unavailable",
    }),
    "Adele - Hello: Video unavailable",
  );
});

test("counts duplicate tracks separately from skipped tracks", async () => {
  const { exportExclusionCounts } = await loadModule();

  assert.deepEqual(
    exportExclusionCounts({
      invalid: 1,
      unmatched: 2,
      unselected: 1,
      deduplicated: 3,
    }),
    { skipped: 4, deduplicated: 3 },
  );
});

test("formats FastAPI validation arrays without object coercion", async () => {
  const { apiErrorMessage } = await loadModule();

  const message = apiErrorMessage(
    {
      detail: [
        {
          loc: ["body", "tracks", 0, "artist"],
          msg: "String should have at least 1 character",
        },
      ],
    },
    "HTTP 422",
  );

  assert.equal(
    message,
    "tracks.0.artist: String should have at least 1 character",
  );
  assert.doesNotMatch(message, /\[object Object\]/);
});

test("labels every backend unmatched reason", async () => {
  const { unmatchedReasonLabel } = await loadModule();

  assert.equal(unmatchedReasonLabel("not_found"), "검색 결과 없음");
  assert.equal(unmatchedReasonLabel("unusable_result"), "사용 가능한 결과 없음");
  assert.equal(unmatchedReasonLabel("low_confidence"), "확신도 부족");
  assert.equal(unmatchedReasonLabel("duplicate_video"), "동일 영상 중복");
});

test("sends a bounded match request and returns the response", async () => {
  const { fetchYouTubeMatches } = await loadModule();
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    return {
      ok: true,
      status: 200,
      json: async () => ({ matched: [], unmatched: [] }),
    };
  };

  const response = await fetchYouTubeMatches(
    fetchImpl,
    "https://api.example",
    "similar",
    [{ name: "Hello", artist: "Adele" }],
    "export-token",
    100,
  );

  assert.deepEqual(response, { matched: [], unmatched: [] });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.example/exports/youtube/matches");
  assert.deepEqual(JSON.parse(calls[0].init.body), {
    bucket: "similar",
    tracks: [{ name: "Hello", artist: "Adele" }],
  });
  assert.equal(
    calls[0].init.headers["X-Side-B-Export-Token"],
    "export-token",
  );
  assert.ok(calls[0].init.signal);
});

test("aborts a match request after the configured timeout", async () => {
  const { fetchYouTubeMatches } = await loadModule();
  const fetchImpl = async (_url, init) =>
    new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      });
    });

  await assert.rejects(
    fetchYouTubeMatches(
      fetchImpl,
      "https://api.example",
      "similar",
      [{ name: "Hello", artist: "Adele" }],
      "export-token",
      1,
    ),
    { name: "AbortError" },
  );
});

test("rejects a missing export token before sending a request", async () => {
  const { fetchYouTubeMatches } = await loadModule();
  let called = false;

  await assert.rejects(
    fetchYouTubeMatches(
      async () => {
        called = true;
      },
      "https://api.example",
      "similar",
      [{ name: "Hello", artist: "Adele" }],
      "  ",
      100,
    ),
    /토큰을 입력/,
  );
  assert.equal(called, false);
});

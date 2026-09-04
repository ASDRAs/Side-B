const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "buckets.js"),
    "utf8",
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

const track = (name) => ({ name, artist: "윤하" });

test("direct 응답은 실행된 세 방향만 라벨 순서로 돌려준다", async () => {
  const { executedBuckets } = await loadModule();

  const buckets = executedBuckets({
    hidden: [track("숨은 곡")],
    similar: [track("비슷한 곡")],
    reverse: [],
  });

  assert.deepEqual(
    buckets.map((bucket) => bucket.name),
    ["similar", "reverse", "hidden"],
  );
});

test("mood 응답도 같은 규칙으로 세 방향을 돌려준다", async () => {
  const { executedBuckets } = await loadModule();

  const buckets = executedBuckets({
    similar: [track("비슷한 곡")],
    opposite: [],
    hidden: [],
  });

  assert.deepEqual(
    buckets.map((bucket) => bucket.name),
    ["similar", "opposite", "hidden"],
  );
});

test("실행되지 않은 방향은 탭에서 빠지고 빈 배열은 남는다", async () => {
  const { executedBuckets } = await loadModule();

  // response_model_exclude_none=True라 미실행 방향은 키 자체가 없다.
  // 방어적으로 null이 들어와도 같은 취급이어야 한다.
  const buckets = executedBuckets({
    similar: [track("비슷한 곡")],
    reverse: [],
    opposite: null,
  });

  assert.deepEqual(
    buckets.map((bucket) => [bucket.name, bucket.tracks.length]),
    [
      ["similar", 1],
      ["reverse", 0],
    ],
  );
});

test("결과가 전혀 없는 응답에서도 빈 목록을 돌려준다", async () => {
  const { executedBuckets } = await loadModule();

  assert.deepEqual(executedBuckets(undefined), []);
  assert.deepEqual(executedBuckets({}), []);
});

test("기본 선택은 similar이고, 비어 있으면 결과가 있는 첫 방향이다", async () => {
  const { defaultBucketIndex, executedBuckets } = await loadModule();

  const withSimilar = executedBuckets({
    similar: [track("a")],
    reverse: [track("b")],
    hidden: [],
  });
  assert.equal(defaultBucketIndex(withSimilar), 0);

  const emptySimilar = executedBuckets({
    similar: [],
    reverse: [],
    hidden: [track("c")],
  });
  assert.equal(defaultBucketIndex(emptySimilar), 2);

  const allEmpty = executedBuckets({ similar: [], reverse: [], hidden: [] });
  assert.equal(defaultBucketIndex(allEmpty), 0);
});

test("총 곡 수는 실행된 모든 버킷의 합이다", async () => {
  const { executedBuckets, totalTrackCount } = await loadModule();

  const buckets = executedBuckets({
    similar: [track("a"), track("b")],
    reverse: [],
    hidden: [track("c")],
  });

  // 선택된 한 탭만 렌더링해도 사용자에게 알리는 수치는 응답 전체다.
  assert.equal(totalTrackCount(buckets), 3);
});

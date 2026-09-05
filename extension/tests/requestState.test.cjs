const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "requestState.js"),
    "utf8",
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

test("타임아웃과 사용자 취소가 서로 다른 문구로 매핑된다", async () => {
  const { requestErrorMessage, timeoutReason } = await loadModule();

  assert.match(requestErrorMessage(timeoutReason()), /시간이 초과/);
  assert.equal(
    requestErrorMessage(new DOMException("cancelled", "AbortError")),
    "추천 요청을 취소했습니다.",
  );
});

test("그 밖의 오류는 원래 메시지를 그대로 보여준다", async () => {
  const { requestErrorMessage } = await loadModule();

  assert.equal(
    requestErrorMessage(new Error("배포 백엔드 사용에는 팀 백엔드 토큰이 필요합니다.")),
    "배포 백엔드 사용에는 팀 백엔드 토큰이 필요합니다.",
  );
  assert.equal(requestErrorMessage(undefined), "추천 요청에 실패했습니다.");
  assert.equal(requestErrorMessage(new Error("")), "추천 요청에 실패했습니다.");
});

test("타임아웃 직후 사용자 취소가 겹쳐도 최초 원인이 남는다", async () => {
  const { requestErrorMessage, timeoutReason } = await loadModule();

  const controller = new AbortController();
  controller.abort(timeoutReason());
  // 90초를 기다리던 사용자가 타임아웃과 거의 동시에 취소를 누른 상황.
  controller.abort();

  assert.equal(controller.signal.reason.name, "TimeoutError");
  assert.match(requestErrorMessage(controller.signal.reason), /시간이 초과/);
});

test("사용자 취소가 먼저면 타임아웃이 뒤따라도 취소로 남는다", async () => {
  const { requestErrorMessage, timeoutReason } = await loadModule();

  const controller = new AbortController();
  controller.abort();
  controller.abort(timeoutReason());

  assert.equal(controller.signal.reason.name, "AbortError");
  assert.equal(
    requestErrorMessage(controller.signal.reason),
    "추천 요청을 취소했습니다.",
  );
});

test("취소한 controller는 다음 요청을 막지 않는다", async () => {
  const cancelled = new AbortController();
  cancelled.abort();

  // 요청마다 새 controller를 만들면 이전 취소가 다음 요청으로 새지 않는다.
  const next = new AbortController();

  assert.equal(cancelled.signal.aborted, true);
  assert.equal(next.signal.aborted, false);
});

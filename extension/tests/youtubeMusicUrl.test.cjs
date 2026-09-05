const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

async function loadModule() {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "scripts", "youtubeMusicUrl.js"),
    "utf8",
  );
  return import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
}

function searchQuery(url) {
  return new URL(url).searchParams.get("q");
}

test("아티스트와 곡명을 검색어 하나로 합친다", async () => {
  const { youtubeMusicSearchUrl } = await loadModule();

  const url = youtubeMusicSearchUrl({ artist: "Younha", name: "Event Horizon" });

  assert.equal(
    url,
    "https://music.youtube.com/search?q=Younha%20Event%20Horizon",
  );
});

test("한글, 괄호, &, feat.를 안전하게 인코딩한다", async () => {
  const { youtubeMusicSearchUrl } = await loadModule();

  const track = { artist: "윤하 & 이런", name: "혜성 (feat. 정재일)" };
  const url = youtubeMusicSearchUrl(track);

  // 인코딩된 문자열을 그대로 비교하면 무엇이 깨졌는지 알기 어렵다.
  // 왕복시켜 원래 검색어가 그대로 남는지 본다.
  assert.equal(searchQuery(url), "윤하 & 이런 혜성 (feat. 정재일)");
  assert.ok(!url.includes(" "), "URL에 인코딩되지 않은 공백이 남으면 안 된다");
  assert.ok(!url.includes("&q"), "& 가 쿼리 구분자로 새어나가면 안 된다");
});

test("아티스트가 없으면 곡명만 사용한다", async () => {
  const { youtubeMusicSearchUrl } = await loadModule();

  assert.equal(searchQuery(youtubeMusicSearchUrl({ name: "혜성" })), "혜성");
  assert.equal(
    searchQuery(youtubeMusicSearchUrl({ artist: "   ", name: "혜성" })),
    "혜성",
  );
});

test("곡명이 없으면 링크를 만들지 않는다", async () => {
  const { youtubeMusicSearchUrl } = await loadModule();

  assert.equal(youtubeMusicSearchUrl({ artist: "윤하" }), null);
  assert.equal(youtubeMusicSearchUrl({ artist: "윤하", name: "  " }), null);
  assert.equal(youtubeMusicSearchUrl(null), null);
});

test("접근성 이름은 외부 목적지를 밝힌다", async () => {
  const { youtubeMusicSearchLabel } = await loadModule();

  assert.equal(
    youtubeMusicSearchLabel({ artist: "윤하", name: "혜성" }),
    "윤하 - 혜성, YouTube Music에서 찾기",
  );
  assert.equal(
    youtubeMusicSearchLabel({ name: "혜성" }),
    "혜성, YouTube Music에서 찾기",
  );
});

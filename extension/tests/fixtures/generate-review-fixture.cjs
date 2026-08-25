const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("@playwright/test");
const extensionPath = "C:/Users/dg203/desktop/side-b/extension";
const matches = {
  bucket: "similar", requested: 4,
  matched: [
    { name:"Whiplash", artist:"aespa", video_id:"v1", youtube_title:"aespa 에스파 'Whiplash' MV", channel_title:"SMTOWN", confidence:0.93, position:0 },
    { name:"Super Shy", artist:"NewJeans", video_id:"v2", youtube_title:"NewJeans (뉴진스) 'Super Shy' Official MV", channel_title:"HYBE LABELS", confidence:0.9, position:1 },
    { name:"Event Horizon", artist:"YOUNHA", video_id:"v3", youtube_title:"YOUNHA - Event Horizon (Live)", channel_title:"Random Uploader 1234", confidence:0.51, auto_selected:false, position:2 },
  ],
  unmatched: [{ name:"너의 모든 순간", artist:"성시경", reason:"low_confidence", position:3 }],
  deduplicated: 0,
};
(async () => {
  const ctx = await chromium.launchPersistentContext("C:/Users/dg203/AppData/Local/Temp/claude/fixprof", {
    channel:"chromium", headless:true,
    args:[`--disable-extensions-except=${extensionPath}`,`--load-extension=${extensionPath}`],
  });
  let [sw] = ctx.serviceWorkers(); if (!sw) sw = await ctx.waitForEvent("serviceworker");
  const page = await ctx.newPage();
  await page.setViewportSize({ width: 440, height: 900 });
  await page.goto(`chrome-extension://${new URL(sw.url()).host}/sidepanel.html`);
  await page.waitForTimeout(400);
  const base = (await page.locator("#apiBaseUrl").inputValue()).replace(/\/+$/, "");
  await page.route(`${base}/recommend`, r => r.fulfill({status:200,contentType:"application/json",
    body: JSON.stringify({track_name:"Whiplash",artist:"aespa",top_n:10,result:{similar:matches.matched.concat(matches.unmatched).map(t=>({name:t.name,artist:t.artist}))}})}));
  await page.route(`${base}/exports/youtube/matches`, r => r.fulfill({status:200,contentType:"application/json",body:JSON.stringify(matches)}));
  await page.fill("#youtubeExportToken","t");
  await page.fill("#query","aespa - Whiplash");
  await page.click("#submitButton");
  await page.waitForSelector(".bucket");
  await page.locator('.bucket[data-bucket="similar"] .export-button').click();
  await page.waitForSelector(".match-item");
  const inner = await page.locator("#youtubeMatchReview").evaluate(el => el.outerHTML);
  const html = `<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Side-B 매칭 검토 fixture</title>
    <link rel="stylesheet" href="../../sidepanel.css" />
  </head>
  <body>
<!--
  sidepanel.js가 실제로 만든 DOM을 그대로 떠 온 시각 확인용 fixture다.
  손으로 고치지 말고 아래 명령으로 다시 생성한다. 자동 테스트는 이 파일을
  참조하지 않는다.
    node tests/fixtures/generate-review-fixture.cjs
-->
${inner.replace(/ hidden(=""|)/g, "").split("\n").map(l => "    " + l).join("\n")}
  </body>
</html>
`;
  fs.writeFileSync(path.join(extensionPath, "tests/fixtures/sidepanel-review.html"), html, "utf8");
  console.log("fixture 재생성 완료");
  await ctx.close();
})().catch(e => { console.error(e); process.exit(1); });

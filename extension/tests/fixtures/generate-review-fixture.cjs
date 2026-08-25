/**
 * 매칭 검토 화면의 시각 확인용 fixture를 다시 만든다.
 *
 * 손으로 맞춘 markup은 UI가 바뀔 때마다 어긋나므로, sidepanel.js가 실제로 만든
 * DOM을 그대로 떠 온다. 자동 테스트는 이 결과물을 참조하지 않는다.
 *
 *   cd extension && node tests/fixtures/generate-review-fixture.cjs
 */
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const { chromium } = require("@playwright/test");

const extensionPath = path.resolve(__dirname, "..", "..");
const outputPath = path.join(__dirname, "sidepanel-review.html");

const matches = {
  bucket: "similar",
  requested: 4,
  matched: [
    {
      name: "Whiplash",
      artist: "aespa",
      video_id: "v1",
      youtube_title: "aespa 에스파 'Whiplash' MV",
      channel_title: "SMTOWN",
      confidence: 0.93,
      position: 0,
    },
    {
      name: "Super Shy",
      artist: "NewJeans",
      video_id: "v2",
      youtube_title: "NewJeans (뉴진스) 'Super Shy' Official MV",
      channel_title: "HYBE LABELS",
      confidence: 0.9,
      position: 1,
    },
    {
      name: "Event Horizon",
      artist: "YOUNHA",
      video_id: "v3",
      youtube_title: "YOUNHA - Event Horizon (Live)",
      channel_title: "Random Uploader 1234",
      confidence: 0.51,
      auto_selected: false,
      position: 2,
    },
  ],
  unmatched: [
    { name: "너의 모든 순간", artist: "성시경", reason: "low_confidence", position: 3 },
  ],
  deduplicated: 0,
};

function indent(html) {
  // 빈 줄에는 공백을 넣지 않는다. trailing whitespace가 커밋에 섞인다.
  return html
    .split("\n")
    .map((line) => (line.trim() ? `    ${line}` : ""))
    .join("\n");
}

async function main() {
  const profileDir = fs.mkdtempSync(path.join(os.tmpdir(), "side-b-fixture-"));
  const context = await chromium.launchPersistentContext(profileDir, {
    channel: "chromium",
    headless: true,
    args: [
      `--disable-extensions-except=${extensionPath}`,
      `--load-extension=${extensionPath}`,
    ],
  });

  try {
    let [serviceWorker] = context.serviceWorkers();
    if (!serviceWorker) {
      serviceWorker = await context.waitForEvent("serviceworker");
    }
    const extensionId = new URL(serviceWorker.url()).host;

    const page = await context.newPage();
    await page.setViewportSize({ width: 440, height: 900 });
    await page.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await page.waitForSelector("#apiBaseUrl");

    const apiBaseUrl = (await page.locator("#apiBaseUrl").inputValue()).replace(
      /\/+$/,
      "",
    );
    const tracks = [...matches.matched, ...matches.unmatched].map((track) => ({
      name: track.name,
      artist: track.artist,
    }));

    await page.route(`${apiBaseUrl}/recommend`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          track_name: "Whiplash",
          artist: "aespa",
          top_n: 10,
          result: { similar: tracks },
        }),
      }),
    );
    await page.route(`${apiBaseUrl}/exports/youtube/matches`, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(matches),
      }),
    );

    await page.fill("#youtubeExportToken", "fixture-token");
    await page.fill("#query", "aespa - Whiplash");
    await page.click("#submitButton");
    await page.waitForSelector(".bucket");
    await page.locator('.bucket[data-bucket="similar"] .export-button').click();
    await page.waitForSelector(".match-item");

    const review = await page
      .locator("#youtubeMatchReview")
      .evaluate((element) => element.outerHTML);

    fs.writeFileSync(
      outputPath,
      `<!doctype html>
<html lang="ko">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Side-B 매칭 검토 fixture</title>
    <link rel="stylesheet" href="../../sidepanel.css" />
  </head>
  <body>
<!--
  sidepanel.js가 만든 DOM을 그대로 떠 온 시각 확인용 fixture다. 손으로 고치지
  말고 다음 명령으로 다시 생성한다.
    cd extension && node tests/fixtures/generate-review-fixture.cjs
-->
${indent(review.replace(/ hidden(="")?/g, ""))}
  </body>
</html>
`,
      "utf8",
    );
    console.log(`fixture 재생성: ${path.relative(extensionPath, outputPath)}`);
  } finally {
    await context.close();
    fs.rmSync(profileDir, { recursive: true, force: true });
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

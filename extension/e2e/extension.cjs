const fs = require("node:fs");
const path = require("node:path");

const { chromium, expect } = require("@playwright/test");

const EXPECTED_EXTENSION_ID = "hfcclomfoickmehgmdgjdjmiiekaciam";
const extensionPath = path.resolve(__dirname, "..");
const manifestPath = path.join(extensionPath, "manifest.json");

function assertApiOriginIsAllowed(apiBaseUrl) {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const requiredPermission = `${new URL(apiBaseUrl).origin}/*`;
  if (!manifest.host_permissions?.includes(requiredPermission)) {
    throw new Error(
      `manifest.json host_permissions에 ${requiredPermission}를 추가하세요.`,
    );
  }
}

async function launchExtensionPage(testInfo) {
  // Every test gets an explicit empty profile under its own output directory.
  // Reusing a profile would leak chrome.storage state between scenarios.
  const context = await chromium.launchPersistentContext(
    testInfo.outputPath("chromium-profile"),
    {
      channel: "chromium",
      headless: testInfo.project.use.headless !== false,
      args: [
        `--disable-extensions-except=${extensionPath}`,
        `--load-extension=${extensionPath}`,
      ],
    },
  );

  try {
    let [serviceWorker] = context.serviceWorkers();
    if (!serviceWorker) {
      serviceWorker = await context.waitForEvent("serviceworker");
    }
    const extensionId = new URL(serviceWorker.url()).host;
    expect(extensionId).toBe(EXPECTED_EXTENSION_ID);

    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    return { context, page };
  } catch (error) {
    await context.close();
    throw error;
  }
}

// #apiBaseUrl은 설정 패널 안의 "고급 설정" details에 들어 있어 두 겹으로 접혀
// 있다. Playwright의 fill()은 가시성을 요구하므로 접힌 상태에서는 채우지 못하고
// actionability 타임아웃까지 멈춘다. SIDE_B_API_BASE_URL이 없는 실행에서는
// inputValue()만 쓰기 때문에 이 문제가 드러나지 않아, 배포 대상 e2e에서만
// 터진다. 값을 읽기만 하더라도 열어 두는 편이 안전하다.
async function resolveApiBaseUrl(page, configuredApiBaseUrl) {
  await page.locator("#apiBaseUrl").evaluate((input) => {
    for (
      let element = input.closest("details");
      element;
      element = element.parentElement?.closest("details")
    ) {
      element.open = true;
    }
  });

  const apiBaseUrlInput = page.locator("#apiBaseUrl");
  if (configuredApiBaseUrl) {
    await apiBaseUrlInput.fill(configuredApiBaseUrl);
  } else {
    await expect(apiBaseUrlInput).not.toHaveValue("");
  }
  return (await apiBaseUrlInput.inputValue()).replace(/\/+$/, "");
}

async function captureFailure(page, testInfo) {
  if (!page || page.isClosed()) {
    return;
  }
  await page.screenshot({
    path: testInfo.outputPath("sidepanel-failure.png"),
    fullPage: true,
  });
}

module.exports = {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
  resolveApiBaseUrl,
};

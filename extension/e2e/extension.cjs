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
  const context = await chromium.launchPersistentContext("", {
    channel: "chromium",
    headless: testInfo.project.use.headless !== false,
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
    expect(extensionId).toBe(EXPECTED_EXTENSION_ID);

    const page = await context.newPage();
    await page.goto(`chrome-extension://${extensionId}/popup.html`);
    return { context, page };
  } catch (error) {
    await context.close();
    throw error;
  }
}

async function captureFailure(page, testInfo) {
  if (!page || page.isClosed()) {
    return;
  }
  await page.screenshot({
    path: testInfo.outputPath("popup-failure.png"),
    fullPage: true,
  });
}

module.exports = {
  assertApiOriginIsAllowed,
  captureFailure,
  launchExtensionPage,
};

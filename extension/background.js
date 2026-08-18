console.log("Background service worker loaded.");

const OFFSCREEN_DOCUMENT_PATH = "offscreen.html";

let creatingOffscreenDocument = null;

async function hasOffscreenDocument() {
  const offscreenUrl = chrome.runtime.getURL(OFFSCREEN_DOCUMENT_PATH);

  const contexts = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [offscreenUrl],
  });

  return contexts.length > 0;
}

async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) {
    return;
  }

  if (!creatingOffscreenDocument) {
    creatingOffscreenDocument = chrome.offscreen
      .createDocument({
        url: OFFSCREEN_DOCUMENT_PATH,
        reasons: ["USER_MEDIA"],
        justification: "Apply EQ to captured tab audio in the background.",
      })
      .finally(() => {
        creatingOffscreenDocument = null;
      });
  }

  await creatingOffscreenDocument;
}

async function sendToOffscreen(message) {
  await ensureOffscreenDocument();

  const response = await chrome.runtime.sendMessage({
    target: "offscreen",
    ...message,
  });

  if (!response?.ok) {
    throw new Error(response?.error || "Offscreen EQ 처리에 실패했습니다.");
  }

  return response;
}

async function startEq(tabId, preset) {
  await ensureOffscreenDocument();

  const state = await sendToOffscreen({
    type: "GET_STATE",
  });

  if (state.active && state.tabId === tabId) {
    return sendToOffscreen({
      type: "UPDATE_EQ",
      preset,
    });
  }

  if (state.active) {
    await sendToOffscreen({
      type: "STOP_EQ",
    });
  }

  const streamId = await chrome.tabCapture.getMediaStreamId({
    targetTabId: tabId,
  });

  return sendToOffscreen({
    type: "START_EQ",
    tabId,
    streamId,
    preset,
  });
}

async function stopEq() {
  if (!(await hasOffscreenDocument())) {
    return {
      ok: true,
      active: false,
    };
  }

  const response = await sendToOffscreen({
    type: "STOP_EQ",
  });

  await chrome.offscreen.closeDocument();

  return response;
}

async function getEqState() {
  if (!(await hasOffscreenDocument())) {
    return {
      ok: true,
      active: false,
      tabId: null,
    };
  }

  return sendToOffscreen({
    type: "GET_STATE",
  });
}

async function handleMessage(message) {
  switch (message.type) {
    case "START_EQ":
      return startEq(message.tabId, message.preset);

    case "UPDATE_EQ":
      return sendToOffscreen({
        type: "UPDATE_EQ",
        preset: message.preset,
      });

    case "STOP_EQ":
      return stopEq();

    case "GET_EQ_STATE":
      return getEqState();

    default:
      throw new Error(`알 수 없는 EQ 메시지: ${message.type}`);
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  console.log("Background received:", message);

  if (message.target !== "background") {
    return false;
  }

  handleMessage(message)
    .then(sendResponse)
    .catch((error) => {
      console.error("Background EQ error:", error);

      sendResponse({
        ok: false,
        error: error?.message || "알 수 없는 오류가 발생했습니다.",
      });
    });

  return true;
});
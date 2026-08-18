async function sendEqMessage(message) {
  const response = await chrome.runtime.sendMessage({
    target: "background",
    ...message,
  });

  if (!response?.ok) {
    throw new Error(response?.error || "EQ 처리에 실패했습니다.");
  }

  return response;
}

async function getActiveTabId() {
  const [tab] = await chrome.tabs.query({
    active: true,
    currentWindow: true,
  });

  if (!tab?.id) {
    throw new Error("현재 탭을 찾을 수 없습니다.");
  }

  return tab.id;
}

export async function startEq(preset) {
  const tabId = await getActiveTabId();

  return sendEqMessage({
    type: "START_EQ",
    tabId,
    preset,
  });
}

export async function updateEq(preset) {
  return sendEqMessage({
    type: "UPDATE_EQ",
    preset,
  });
}

export async function stopEq() {
  return sendEqMessage({
    type: "STOP_EQ",
  });
}

export async function getEqState() {
  return sendEqMessage({
    type: "GET_EQ_STATE",
  });
}
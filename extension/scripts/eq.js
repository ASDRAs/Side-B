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

export async function startEq(mode = "auto") {
  const { id: windowId } = await chrome.windows.getCurrent();
  return sendEqMessage({
    type: "START_EQ",
    windowId,
    mode,
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

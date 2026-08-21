async function sendYouTubeExportMessage(message) {
  const response = await chrome.runtime.sendMessage({
    target: "background",
    ...message,
  });

  if (!response?.ok) {
    throw new Error(response?.error || "YouTube 내보내기에 실패했습니다.");
  }

  return response;
}

export async function createYouTubePlaylist(payload) {
  return sendYouTubeExportMessage({
    type: "CREATE_YOUTUBE_PLAYLIST",
    payload,
  });
}

export async function getYouTubeExportState() {
  const response = await sendYouTubeExportMessage({
    type: "GET_YOUTUBE_EXPORT_STATE",
  });
  return response.state || null;
}

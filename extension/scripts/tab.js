import { getCurrentYouTubeMusicTrack } from "./youtubeMusic.js";

export async function readCurrentTrack() {
  const [tab] = await chrome.tabs.query({
    active: true,
    currentWindow: true,
  });

  if (!tab?.id) {
    return null;
  }

  if (!tab.url?.startsWith("https://music.youtube.com/")) {
    return null;
  }

  const [result] = await chrome.scripting.executeScript({
    target: {
      tabId: tab.id,
    },
    world: "MAIN",
    func: getCurrentYouTubeMusicTrack,
  });

  return result?.result ?? null;
}
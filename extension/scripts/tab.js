import { getCurrentYouTubeMusicTrack } from "./youtubeMusic.js";

export class NoMusicTabError extends Error {
  constructor() {
    super("YouTube Music 탭이 열려 있지 않습니다.");
    this.name = "NoMusicTabError";
  }
}

async function getMusicTabId() {
  const response = await chrome.runtime.sendMessage({
    target: "background",
    type: "GET_MUSIC_TAB",
  });

  if (!response?.ok) {
    throw new Error(response?.error || "YouTube Music 탭을 확인하지 못했습니다.");
  }

  return response.tabId;
}

export async function readCurrentTrack() {
  const tabId = await getMusicTabId();

  if (!Number.isInteger(tabId)) {
    throw new NoMusicTabError();
  }

  const [result] = await chrome.scripting.executeScript({
    target: {
      tabId,
    },
    world: "MAIN",
    func: getCurrentYouTubeMusicTrack,
  });

  return result?.result ?? null;
}

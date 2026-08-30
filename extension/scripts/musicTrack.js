// Shared by the classic service worker and the side panel's module wrapper.
globalThis.SideBMusic = {
  readTrack: function readTrack() {
    if (window.location.hostname !== "music.youtube.com") return null;

    const metadata = navigator.mediaSession?.metadata;
    const playerBar = document.querySelector("ytmusic-player-bar");
    const byline = playerBar?.querySelector(".byline");
    const title = metadata?.title?.trim() ||
      playerBar?.querySelector(".title")?.textContent?.trim();
    if (!title) return null;

    return {
      title,
      artist: metadata?.artist?.trim() ||
        byline?.querySelector("a")?.textContent?.trim() ||
        byline?.textContent?.split("\u2022")[0]?.trim() || null,
      videoId: new URL(window.location.href).searchParams.get("v"),
      url: window.location.href,
    };
  },
};

export function getCurrentYouTubeMusicTrack() {
  if (window.location.hostname !== "music.youtube.com") {
    return null;
  }

  const metadata = navigator.mediaSession?.metadata;

  if (metadata?.title) {
    return {
      title: metadata.title.trim(),
      artist: metadata.artist?.trim() || null,
    };
  }

  const playerBar = document.querySelector("ytmusic-player-bar");

  if (!playerBar) {
    return null;
  }

  const title = playerBar.querySelector(".title")?.textContent?.trim();
  const byline = playerBar.querySelector(".byline");

  const artist =
    byline?.querySelector("a")?.textContent?.trim() ||
    byline?.textContent?.split("•")[0]?.trim() ||
    null;

  if (!title) {
    return null;
  }

  return {
    title,
    artist,
  };
}
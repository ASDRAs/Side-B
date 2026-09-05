// 개별 곡 탐색은 검색 URL만 만든다. 백엔드 매칭도 YouTube Data API도 거치지
// 않으므로 일일 quota와 Google OAuth를 쓰지 않는다. 특정 영상을 고르는 일은
// 플레이리스트 내보내기의 엄격한 매칭 흐름에 남겨 둔다.
const SEARCH_ENDPOINT = "https://music.youtube.com/search";

function trimmed(value) {
  return String(value ?? "").trim();
}

export function youtubeMusicSearchUrl(track) {
  const name = trimmed(track?.name);
  // 곡명이 없으면 검색어가 아티스트 하나로 뭉개진다. 링크를 만들지 않는다.
  if (!name) {
    return null;
  }

  const artist = trimmed(track?.artist);
  const query = artist ? `${artist} ${name}` : name;
  return `${SEARCH_ENDPOINT}?q=${encodeURIComponent(query)}`;
}

export function youtubeMusicSearchLabel(track) {
  const name = trimmed(track?.name);
  const artist = trimmed(track?.artist);
  const subject = artist ? `${artist} - ${name}` : name;
  return `${subject}, YouTube Music에서 찾기`;
}

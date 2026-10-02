const GENRE_LABELS = {
  pop: "POP",
  rnb_soul: "R&B/Soul",
  dance: "댄스",
  hiphop: "랩/힙합",
  rock_metal: "록/메탈",
  ballad: "발라드",
  blues: "블루스",
  jazz: "재즈",
  country: "컨트리",
  folk: "포크",
  jpop: "J-POP",
  folk_blues_country: "포크/블루스/컨트리",
};

export function genreDisplayName(genre) {
  return Object.hasOwn(GENRE_LABELS, genre) ? GENRE_LABELS[genre] : genre;
}

export function eqStatusText(state) {
  switch (state?.status) {
    case "awaiting_activation":
      return "음악 탭에서 툴바의 Side-B 아이콘을 눌러 주세요. 캡처 대기는 2분 후 취소됩니다.";
    case "waiting_track":
      return "곡 정보 대기 · 원음 재생 중";
    case "analyzing":
      return "곡별 EQ 분석 중 · 원음 재생 중";
    case "unavailable":
      return state.error ? `${state.error} · 원음 재생 중` : "지원하는 장르 프리셋 없음 · 원음 재생 중";
    case "applied":
      return state.mode === "test" ? "테스트 EQ 적용 중 · 1 kHz 감쇠" :
        (state.genre ? `${genreDisplayName(state.genre)} EQ 적용 중` : "곡별 EQ 적용 중");
    case "suspended":
      return "오디오 출력이 중단됐습니다. EQ 상세에서 다시 적용을 눌러 주세요.";
    case "error":
      return `EQ 적용 실패: ${state.error || "오디오 연결 오류"}`;
    default:
      return "EQ가 적용되지 않았습니다.";
  }
}

globalThis.SideBEqProvider = {
  async getPreset(track, { signal }) {
    // AI 연결 지점: 별도 곡/장르 분류 모델이 준비되면 이 함수만 연결한다.
    // 입력 track: { title, artist, videoId, url }, signal: 취소용 AbortSignal.
    // 출력: { preamp: dB(-30..0), bands: [{ frequency: Hz(20..20000),
    //         gain: dB(-12..12), q?: 0.1..18 }] 또는 null(분류/프리셋 없음).
    // 장르별 EQ 규칙, 모델 종류, 오디오 전달 방식은 여기서 임의로 정하지 않는다.
    // 실제 API 연결 시 signal을 fetch에 전달하고 키를 코드에 넣지 않는다.
    // offscreen이 시간 제한·응답 검증·곡 변경 시 취소·원음 복귀를 담당한다.
    void track;
    void signal;
    return null;
  },
};

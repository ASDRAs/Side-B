// 추천 요청은 타임아웃과 사용자 취소라는 두 경로로 중단된다. 둘 다 AbortError로
// 뭉뚱그리면 "시간 초과"와 "취소했습니다"를 구분할 수 없다.
//
// 그래서 하나의 AbortController를 서로 다른 reason으로 중단한다. abort()는 최초
// reason만 남기므로, 타임아웃 직후 사용자가 취소를 눌러도 원인이 뒤바뀌지 않는다.
// boolean 플래그로 원인을 추정하면 그 순서 경쟁에서 틀린 문구가 나온다.
export function timeoutReason() {
  return new DOMException("Recommendation timed out", "TimeoutError");
}

export function requestErrorMessage(error) {
  if (error?.name === "TimeoutError") {
    return "요청 시간이 초과되었습니다. 백엔드 로그를 확인하세요.";
  }
  if (error?.name === "AbortError") {
    return "추천 요청을 취소했습니다.";
  }
  return error?.message || "추천 요청에 실패했습니다.";
}

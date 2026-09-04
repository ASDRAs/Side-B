// 백엔드는 한 요청에서 네 방향을 모두 실행하지 않는다. direct 검색은
// similar/reverse/hidden, mood 검색은 similar/opposite/hidden이고,
// /recommend 라우터가 response_model_exclude_none=True를 쓰므로 실행하지 않은
// 방향은 null이 아니라 응답에서 키 자체가 빠진다.
//
// 그래서 "배열이 아님 = 실행하지 않은 방향", "빈 배열 = 실행했지만 0곡"이다.
// 둘을 0곡으로 합치면 사용자가 알고리즘이 돌았는지 알 수 없어진다.
export const BUCKET_LABELS = {
  similar: "유사한 곡",
  reverse: "저노출 유사곡",
  opposite: "반대 무드",
  hidden: "숨겨진 곡",
};

export function executedBuckets(result) {
  return Object.keys(BUCKET_LABELS)
    .filter((name) => Array.isArray(result?.[name]))
    .map((name) => ({
      name,
      label: BUCKET_LABELS[name],
      tracks: result[name],
    }));
}

export function defaultBucketIndex(buckets) {
  const similar = buckets.findIndex((bucket) => bucket.name === "similar");
  if (similar !== -1 && buckets[similar].tracks.length > 0) {
    return similar;
  }

  // similar가 비었으면 결과가 있는 첫 방향을 연다. 전부 비었으면 첫 탭이다.
  const withTracks = buckets.findIndex((bucket) => bucket.tracks.length > 0);
  return withTracks === -1 ? 0 : withTracks;
}

// 화면에는 선택한 한 탭만 렌더링하므로 총 곡 수를 DOM에서 셀 수 없다.
export function totalTrackCount(buckets) {
  return buckets.reduce((sum, bucket) => sum + bucket.tracks.length, 0);
}

import { apiErrorMessage } from "./apiConfig.js";

export function partitionExportableTracks(tracks, limit = 10) {
  const source = Array.isArray(tracks) ? tracks.slice(0, limit) : [];
  const valid = [];
  let invalid = 0;

  for (const track of source) {
    const name = String(track?.name || "").trim();
    const artist = String(track?.artist || "").trim();
    if (!name || !artist || name.length > 200 || artist.length > 200) {
      invalid += 1;
      continue;
    }
    valid.push({ name, artist });
  }
  return { valid, invalid, requested: source.length };
}

export function orderedMatchReviewRows(matches = {}) {
  const rows = [];
  for (const [index, track] of (matches.matched || []).entries()) {
    rows.push({ kind: "matched", index, track, order: rows.length });
  }
  for (const track of matches.unmatched || []) {
    rows.push({ kind: "unmatched", index: null, track, order: rows.length });
  }
  rows.sort((left, right) => {
    const leftPosition = Number.isInteger(left.track?.position)
      ? left.track.position
      : Number.MAX_SAFE_INTEGER;
    const rightPosition = Number.isInteger(right.track?.position)
      ? right.track.position
      : Number.MAX_SAFE_INTEGER;
    return leftPosition - rightPosition || left.order - right.order;
  });
  return rows.map(({ order: _order, ...row }) => row);
}

export function shouldAutoSelectMatch(track) {
  // Older backends do not send this field, so preserve their previous default.
  return track?.auto_selected !== false;
}

export function isStateForOperation(state, operationId) {
  return Boolean(operationId && state?.operationId === operationId);
}

export function failedTrackLabel(failure) {
  const identity = [failure?.artist, failure?.name]
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .join(" - ");
  const message = String(failure?.error || "추가 실패").trim();
  return identity ? `${identity}: ${message}` : message;
}

export function exportExclusionCounts({
  invalid = 0,
  unmatched = 0,
  unselected = 0,
  deduplicated = 0,
} = {}) {
  return {
    skipped: invalid + unmatched + unselected,
    deduplicated,
  };
}

export function unmatchedReasonLabel(reason) {
  return (
    {
      not_found: "검색 결과 없음",
      unusable_result: "사용 가능한 결과 없음",
      low_confidence: "확신도 부족",
      duplicate_video: "동일 영상 중복",
    }[reason] || "매칭 제외"
  );
}

export async function fetchYouTubeMatches(
  fetchImpl,
  apiBaseUrl,
  bucketName,
  tracks,
  exportToken,
  timeoutMs,
) {
  const token = String(exportToken || "").trim();
  if (!token) {
    throw new Error("YouTube 내보내기 토큰을 입력하세요.");
  }
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetchImpl(`${apiBaseUrl}/exports/youtube/matches`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Side-B-Export-Token": token,
      },
      body: JSON.stringify({ bucket: bucketName, tracks }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const fallback = `HTTP ${response.status}`;
      let message = fallback;
      try {
        message = apiErrorMessage(await response.json(), fallback);
      } catch {
        // The status is sufficient when the backend did not return JSON.
      }
      throw new Error(message);
    }
    return response.json();
  } finally {
    clearTimeout(timeoutId);
  }
}

import type {
  RecommendRequest,
  RecommendResponse,
  TrackRecommendation,
} from './types';

export function resolveApiBaseUrl(): string {
  const fromEnv = import.meta.env.VITE_API_BASE_URL?.trim();
  if (fromEnv) {
    return fromEnv.replace(/\/$/, '');
  }

  const { protocol, hostname } = window.location;
  if (
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '::1' ||
    hostname === '[::1]'
  ) {
    return `${protocol}//127.0.0.1:8000`;
  }

  return `${protocol}//${hostname}:8000`;
}

export async function recommend(request: RecommendRequest): Promise<RecommendResponse> {
  const response = await fetch(`${resolveApiBaseUrl()}/recommend`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`추천 API 호출 실패: ${response.status}`);
  }

  const payload = (await response.json()) as RecommendResponse;
  return {
    ...payload,
    result: {
      similar: payload.result?.similar ?? [],
      reverse: payload.result?.reverse ?? [],
      opposite: payload.result?.opposite ?? [],
      hidden: payload.result?.hidden ?? [],
    },
  };
}

/**
 * 추천 응답의 `source_id`(`itunes:123` 형태)를 공급자와 ID로 쪼갠다.
 * 형식이 다르면 null을 돌려주고 호출부가 곡명 경로로 되돌아간다.
 */
function splitSourceId(sourceId?: string | null): [string, string] | null {
  const separator = sourceId?.indexOf(':') ?? -1;
  if (!sourceId || separator <= 0) return null;
  const provider = sourceId.slice(0, separator);
  const providerTrackId = sourceId.slice(separator + 1);
  if (!providerTrackId) return null;
  return [provider, providerTrackId];
}

/**
 * ID가 있으면 그것으로 재생한다. 서버가 공급자 검색을 건너뛰므로 카탈로그
 * 표기가 화면의 곡명과 달라도 정확히 같은 곡이 나온다. ID가 없으면 곡명으로
 * 검색하는 기존 경로를 쓴다.
 */
export function previewStreamUrl(track: TrackRecommendation): string {
  const identity = splitSourceId(track.source_id);
  const params = identity
    ? new URLSearchParams({ provider: identity[0], provider_track_id: identity[1] })
    : new URLSearchParams({ track: track.name, artist: track.artist });
  return `${resolveApiBaseUrl()}/preview/stream?${params.toString()}`;
}

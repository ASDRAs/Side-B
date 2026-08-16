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
 * 서버가 ID로 조회할 수 있는 공급자. `source_id`에는 `lastfm:*`처럼 조회
 * 대상이 아닌 값도 올 수 있는데, 그걸 ID 경로로 보내면 곡명으로는 찾을 수
 * 있었을 곡이 404가 된다.
 */
const LOOKUP_PROVIDERS = new Set(['itunes', 'deezer']);

/**
 * 추천 응답의 `source_id`(`itunes:123` 형태)를 공급자와 ID로 쪼갠다.
 * 조회할 수 없는 값이면 null을 돌려주고 호출부가 곡명 경로로 되돌아간다.
 */
function splitSourceId(sourceId?: string | null): [string, string] | null {
  const separator = sourceId?.indexOf(':') ?? -1;
  if (!sourceId || separator <= 0) return null;
  const provider = sourceId.slice(0, separator);
  const providerTrackId = sourceId.slice(separator + 1);
  if (!providerTrackId || !LOOKUP_PROVIDERS.has(provider)) return null;
  return [provider, providerTrackId];
}

/**
 * ID가 있으면 그것으로 조회하고, 없으면 곡명으로 검색한다. 추천 후보는 이제
 * `source_id`가 비어 있어 대부분 곡명 경로를 타지만, 기준곡처럼 ID가 실려 오는
 * 곡은 계속 정확 조회를 쓴다.
 */
function previewParams(track: TrackRecommendation): string {
  const identity = splitSourceId(track.source_id);
  const params = identity
    ? new URLSearchParams({ provider: identity[0], provider_track_id: identity[1] })
    : new URLSearchParams({ track: track.name, artist: track.artist });
  return params.toString();
}

export interface ResolvedPreview {
  provider: string;
  providerTrackId: string;
  /** 공급자 CDN 주소. 만료 시각이 들어 있으므로 즉시 재생해야 한다. */
  previewUrl: string;
  artworkUrl: string | null;
}

/**
 * 재생할 곡을 확정한다. 응답 하나에 CDN 주소, 공급자 ID, 앨범아트가 함께 온다.
 *
 * 추천 응답은 더 이상 후보의 앨범아트를 채우지 않는다. 곡마다 공급자를 부르면
 * 추천 한 번에 30회가 나가는데 iTunes 상한이 분당 20회이기 때문이다. 그래서
 * 재생하는 곡만 이 시점에 확정한다.
 *
 * 확정은 이 한 번뿐이다. 스트림 프록시를 함께 부르면 서버 캐시가 프로세스
 * 로컬이라 다른 인스턴스에서 같은 곡을 또 조회하게 되고, 장애 중에는 들리는
 * 음원과 보이는 앨범아트가 다른 binding에서 나올 수도 있다.
 */
export async function resolvePreview(
  track: TrackRecommendation,
): Promise<ResolvedPreview | null> {
  const response = await fetch(`${resolveApiBaseUrl()}/preview?${previewParams(track)}`);
  if (!response.ok) return null;
  const payload = (await response.json()) as {
    preview_url?: string;
    provider?: string;
    provider_track_id?: string;
    artwork_url?: string | null;
  };
  if (!payload.preview_url || !payload.provider || !payload.provider_track_id) {
    return null;
  }
  return {
    provider: payload.provider,
    providerTrackId: payload.provider_track_id,
    previewUrl: payload.preview_url,
    artworkUrl: payload.artwork_url ?? null,
  };
}

/**
 * CDN 재생이 막혔을 때만 쓰는 우회로. 확정된 ID로 요청하므로 서버는 검색이
 * 아니라 조회를 하고, 방금 확정한 것과 같은 음원이 나온다.
 */
export function previewStreamUrl(resolved: ResolvedPreview): string {
  const params = new URLSearchParams({
    provider: resolved.provider,
    provider_track_id: resolved.providerTrackId,
  });
  return `${resolveApiBaseUrl()}/preview/stream?${params.toString()}`;
}

import type { RecommendRequest, RecommendResponse } from './types';

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

export function previewStreamUrl(track: string, artist: string): string {
  const params = new URLSearchParams({ track, artist });
  return `${resolveApiBaseUrl()}/preview/stream?${params.toString()}`;
}

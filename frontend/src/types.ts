export type RecommendationBucket = 'similar' | 'reverse' | 'opposite' | 'hidden';

export interface RecommendRequest {
  query: string;
  top_n: number;
}

export interface TrackRecommendation {
  name: string;
  artist: string;
  source_id?: string | null;
  album_art_url?: string | null;
  /**
   * 후보 풀 안에서의 상대 노출도(0~100). 예전에는 Deezer rank 기반 절대값이었다.
   * 지금은 요청마다 풀이 달라지므로 요청 사이에 비교하면 안 된다.
   */
  popularity?: number | null;
  /** popularity가 어느 신호에서 나왔는지. 'none'이면 노출도를 알 수 없다는 뜻이다. */
  exposure_source?: 'listeners' | 'playcount' | 'tag_rank' | 'none';
  match_score?: number | null;
  tag_rank?: number | null;
  reverse_score?: number | null;
  algo?: string;
  label?: string;
  reason_tags?: string[];
}

export interface RecommendResponse {
  track_name: string;
  artist: string;
  top_n: number;
  source_id?: string | null;
  album_art_url?: string | null;
  result: Record<RecommendationBucket, TrackRecommendation[]>;
}

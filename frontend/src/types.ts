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
  popularity?: number | null;
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

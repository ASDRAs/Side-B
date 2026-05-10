import { FormEvent, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Clock3,
  Disc3,
  Loader2,
  Pause,
  Play,
  RotateCcw,
  Search,
  Sparkles,
  Volume2,
  X,
} from 'lucide-react';

import { previewStreamUrl, recommend } from './api';
import type { RecommendationBucket, RecommendResponse, TrackRecommendation } from './types';

import searchImage from '../assets/images/search.png';

const buckets: Array<{
  key: RecommendationBucket;
  title: string;
  shortTitle: string;
  description: string;
  tone: string;
}> = [
  {
    key: 'similar',
    title: '취향이 겹치는 곡들',
    shortTitle: '유사',
    description: '청취 패턴이 가까운 곡',
    tone: 'teal',
  },
  {
    key: 'reverse',
    title: '밀려난 유사곡들',
    shortTitle: 'Reverse',
    description: '상위권 밖의 유사 후보',
    tone: 'rose',
  },
  {
    key: 'opposite',
    title: '반대 무드의 곡들',
    shortTitle: '반대',
    description: '감정선이 다른 곡',
    tone: 'slate',
  },
  {
    key: 'hidden',
    title: '숨은 곡 후보',
    shortTitle: 'Hidden',
    description: '다른 아티스트의 발견',
    tone: 'gold',
  },
];

const examples = ['Younha Event Horizon', '아이유 너랑나', 'Bohemian Rhapsody Queen', '새벽감성 음악'];

interface PlayerState {
  key: string;
  track: TrackRecommendation;
  status: 'loading' | 'playing' | 'paused';
}

export default function App() {
  const [query, setQuery] = useState('');
  const [topN, setTopN] = useState(10);
  const [response, setResponse] = useState<RecommendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [activeBucket, setActiveBucket] = useState<RecommendationBucket>('similar');
  const [player, setPlayer] = useState<PlayerState | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const allTracks = useMemo(() => {
    if (!response) return [];
    return buckets.flatMap((bucket) => response.result[bucket.key]);
  }, [response]);

  const seedArt = response?.album_art_url || allTracks.find((track) => track.album_art_url)?.album_art_url || searchImage;

  async function submitSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const keyword = query.trim();
    if (!keyword || loading) return;

    setLoading(true);
    setError('');
    stopPreview();

    try {
      const result = await recommend({ query: keyword, top_n: topN });
      setResponse(result);
      setActiveBucket('similar');
      setHistory((prev) => [keyword, ...prev.filter((item) => item !== keyword)].slice(0, 6));
    } catch (err) {
      setError(err instanceof Error ? err.message : '추천 결과를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }

  function resetSearch() {
    stopPreview();
    setResponse(null);
    setError('');
  }

  function stopPreview() {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    setPlayer(null);
  }

  function togglePreview(track: TrackRecommendation) {
    const key = `${track.artist}::${track.name}`;
    if (player?.key === key && audioRef.current) {
      if (audioRef.current.paused) {
        void audioRef.current.play();
        setPlayer({ key, track, status: 'playing' });
      } else {
        audioRef.current.pause();
        setPlayer({ key, track, status: 'paused' });
      }
      return;
    }

    stopPreview();
    const audio = new Audio(previewStreamUrl(track.name, track.artist));
    audioRef.current = audio;
    setPlayer({ key, track, status: 'loading' });
    audio.addEventListener('playing', () => setPlayer({ key, track, status: 'playing' }));
    audio.addEventListener('pause', () => setPlayer((current) => (current?.key === key ? { key, track, status: 'paused' } : current)));
    audio.addEventListener('ended', stopPreview);
    audio.addEventListener('error', stopPreview);
    void audio.play();
  }

  const selectedTracks = response?.result[activeBucket] ?? [];

  return (
    <main className="app-shell">
      <section className={`workspace ${response ? 'has-results' : ''}`}>
        <aside className="search-panel">
          <div className="brand-row">
            <div className="brand-mark">
              <Disc3 size={24} />
            </div>
            <div>
              <h1>Side-B</h1>
              <p>들리지 않던 쪽으로, 취향의 이면을 넘기다</p>
            </div>
          </div>

          <form className="search-form" onSubmit={submitSearch}>
            <label htmlFor="query">검색어</label>
            <div className="search-input-wrap">
              <Search size={20} />
              <input
                id="query"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="곡, 아티스트, 무드"
                disabled={loading}
              />
              {query && (
                <button type="button" className="icon-button ghost" onClick={() => setQuery('')} aria-label="검색어 지우기">
                  <X size={18} />
                </button>
              )}
            </div>

            <div className="search-options">
              <label htmlFor="topN">개수</label>
              <input
                id="topN"
                type="range"
                min="4"
                max="20"
                value={topN}
                onChange={(event) => setTopN(Number(event.target.value))}
              />
              <span>{topN}</span>
            </div>

            <button className="primary-button" type="submit" disabled={loading || !query.trim()}>
              {loading ? <Loader2 className="spin" size={19} /> : <Sparkles size={19} />}
              <span>{loading ? '탐색 중' : '탐색 시작'}</span>
            </button>
          </form>

          <div className="example-list">
            {examples.map((item) => (
              <button key={item} type="button" onClick={() => setQuery(item)}>
                {item}
              </button>
            ))}
          </div>

          {history.length > 0 && (
            <div className="history-list">
              <div className="mini-heading">
                <Clock3 size={15} />
                <span>최근 탐색</span>
              </div>
              {history.map((item) => (
                <button key={item} type="button" onClick={() => setQuery(item)}>
                  {item}
                </button>
              ))}
            </div>
          )}
        </aside>

        <section className="result-panel">
          {!response ? (
            <div className="empty-state">
              <div className="record-preview">
                <img src={searchImage} alt="Side-B preview" />
              </div>
              <div className="empty-copy">
                <h2>취향의 뒷면을 펼쳐볼 검색어를 입력하세요</h2>
                <p>한 곡에서 시작해 닮은 곡, 반대 무드, 숨은 후보를 한 화면에서 정리합니다.</p>
              </div>
            </div>
          ) : (
            <div className="results">
              <header className="result-header">
                <button className="icon-button" type="button" onClick={resetSearch} aria-label="검색 화면으로 돌아가기">
                  <ArrowLeft size={20} />
                </button>
                <div className="seed-card">
                  <img src={seedArt} alt="" />
                  <div>
                    <span>기준 트랙</span>
                    <h2>{response.track_name}</h2>
                    <p>{response.artist}</p>
                  </div>
                </div>
                <button className="icon-button" type="button" onClick={() => void submitSearch()} aria-label="다시 탐색">
                  <RotateCcw size={19} />
                </button>
              </header>

              {error && <div className="error-box">{error}</div>}

              <div className="bucket-tabs" role="tablist" aria-label="추천 그룹">
                {buckets.map((bucket) => (
                  <button
                    key={bucket.key}
                    type="button"
                    className={activeBucket === bucket.key ? 'active' : ''}
                    onClick={() => setActiveBucket(bucket.key)}
                  >
                    <span>{bucket.shortTitle}</span>
                    <strong>{response.result[bucket.key].length}</strong>
                  </button>
                ))}
              </div>

              <div className="bucket-summary" data-tone={buckets.find((bucket) => bucket.key === activeBucket)?.tone}>
                <div>
                  <h3>{buckets.find((bucket) => bucket.key === activeBucket)?.title}</h3>
                  <p>{buckets.find((bucket) => bucket.key === activeBucket)?.description}</p>
                </div>
                <span>{selectedTracks.length} tracks</span>
              </div>

              <div className="track-grid">
                {selectedTracks.length === 0 ? (
                  <div className="no-results">표시할 추천 곡이 없습니다.</div>
                ) : (
                  selectedTracks.map((track, index) => {
                    const key = `${track.artist}::${track.name}`;
                    return (
                      <TrackCard
                        key={`${key}-${index}`}
                        index={index + 1}
                        track={track}
                        active={player?.key === key}
                        status={player?.key === key ? player.status : 'paused'}
                        onToggle={() => togglePreview(track)}
                      />
                    );
                  })
                )}
              </div>
            </div>
          )}
        </section>
      </section>

      {player && (
        <div className="mini-player">
          <img src={player.track.album_art_url || searchImage} alt="" />
          <div>
            <strong>{player.track.name}</strong>
            <span>{player.track.artist}</span>
          </div>
          <button className="icon-button" type="button" onClick={() => togglePreview(player.track)} aria-label="미리듣기 재생 전환">
            {player.status === 'loading' ? <Loader2 className="spin" size={18} /> : player.status === 'playing' ? <Pause size={18} /> : <Play size={18} />}
          </button>
          <button className="icon-button" type="button" onClick={stopPreview} aria-label="미리듣기 정지">
            <X size={18} />
          </button>
        </div>
      )}
    </main>
  );
}

function TrackCard({
  index,
  track,
  active,
  status,
  onToggle,
}: {
  index: number;
  track: TrackRecommendation;
  active: boolean;
  status: PlayerState['status'];
  onToggle: () => void;
}) {
  return (
    <article className={`track-card ${active ? 'active' : ''}`}>
      <div className="track-art">
        {track.album_art_url ? <img src={track.album_art_url} alt="" /> : <Disc3 size={28} />}
        <button type="button" onClick={onToggle} aria-label={`${track.name} 미리듣기`}>
          {active && status === 'loading' ? <Loader2 className="spin" size={18} /> : active && status === 'playing' ? <Pause size={18} /> : <Play size={18} />}
        </button>
      </div>
      <div className="track-body">
        <span className="track-index">{String(index).padStart(2, '0')}</span>
        <h4>{track.name}</h4>
        <p>{track.artist}</p>
        <div className="tag-row">
          {(track.reason_tags ?? []).slice(0, 3).map((tag) => (
            <span key={tag}>{tag}</span>
          ))}
          {typeof track.popularity === 'number' && <span>{track.popularity}</span>}
        </div>
      </div>
      {active && (
        <div className="playing-indicator">
          <Volume2 size={15} />
          <span>{status === 'playing' ? 'playing' : status}</span>
        </div>
      )}
    </article>
  );
}

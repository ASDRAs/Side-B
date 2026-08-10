import { FormEvent, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Grid3X3,
  List,
  Loader2,
  Music,
  Pause,
  Play,
  Search,
  X,
} from 'lucide-react';

import { previewStreamUrl, recommend } from './api';
import type { RecommendationBucket, RecommendResponse, TrackRecommendation } from './types';

type View = 'search' | 'map' | 'group';
type TrackViewMode = 'gallery' | 'list';
type PlayerStatus = 'loading' | 'playing' | 'paused';

interface PlayerState {
  key: string;
  track: TrackRecommendation;
  status: PlayerStatus;
}

interface GroupData {
  key: RecommendationBucket;
  label: string;
  description: string;
  color: string;
  positionClass: string;
  sizeClass: string;
}

const groups: GroupData[] = [
  {
    key: 'reverse',
    label: '밀려난 유사곡들',
    description: '상위 추천 밖의 저노출 유사곡',
    color: '#d38fb4',
    positionClass: 'node-reverse',
    sizeClass: 'node-large',
  },
  {
    key: 'similar',
    label: '취향이 겹치는 곡들',
    description: '청취 패턴이 가까운 곡',
    color: '#7cbfb3',
    positionClass: 'node-similar',
    sizeClass: 'node-small',
  },
  {
    key: 'opposite',
    label: '반대 무드의 곡들',
    description: '감정선이 다른 곡',
    color: '#8a95a6',
    positionClass: 'node-opposite',
    sizeClass: 'node-small',
  },
  {
    key: 'hidden',
    label: '닮은 아티스트 곡들',
    description: '다른 아티스트 추천',
    color: '#d4a157',
    positionClass: 'node-hidden',
    sizeClass: 'node-medium',
  },
];

const historyColors = ['#fff3b0', '#ffe8a3', '#fff0c7', '#f8e7a1', '#ffecb5', '#f6e7b5', '#fff6cc'];

const loadingPhrases = [
  '알고리즘의 이면을 탐색 중',
  '취향의 반대편을 뒤집어 보는 중',
  '레코드 홈 사이에서 닮은 결을 찾는 중',
  '숨은 후보를 Side-B로 넘기는 중',
];

export default function App() {
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState<RecommendResponse | null>(null);
  const [view, setView] = useState<View>('search');
  const [activeGroup, setActiveGroup] = useState<GroupData | null>(null);
  const [trackViewMode, setTrackViewMode] = useState<TrackViewMode>('gallery');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [history, setHistory] = useState<string[]>([]);
  const [player, setPlayer] = useState<PlayerState | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const allTracks = useMemo(() => {
    if (!response) return [];
    return groups.flatMap((group) => response.result[group.key] ?? []);
  }, [response]);

  async function submitSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (loading) return;

    const rawInput = query.trim();
    const keyword = rawInput || '너랑 나, IU';
    const apiQuery = buildQuery(keyword);

    setLoading(true);
    setError('');
    stopPreview();

    try {
      const result = await recommend({ query: apiQuery, top_n: 10 });
      const hasAnyResult = groups.some((group) => (result.result[group.key] ?? []).length > 0);
      if (!hasAnyResult) {
        setError('추천 결과를 찾지 못했어요.');
        return;
      }

      setResponse(result);
      setView('map');
      setActiveGroup(null);
      if (rawInput) {
        setHistory((prev) => [rawInput, ...prev.filter((item) => item.toLowerCase() !== rawInput.toLowerCase())].slice(0, 8));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '추천 API 호출에 실패했습니다.');
    } finally {
      setLoading(false);
    }
  }

  function appendKeyword(keyword: string) {
    const current = query.trim();
    if (!current) {
      setQuery(keyword);
      return;
    }
    const tokens = current
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);
    if (tokens.some((item) => item.toLowerCase() === keyword.toLowerCase())) return;
    setQuery(`${current}, ${keyword}`);
  }

  function openGroup(group: GroupData) {
    if (!response || (response.result[group.key] ?? []).length === 0) return;
    setActiveGroup(group);
    setTrackViewMode('gallery');
    setView('group');
  }

  function backToSearch() {
    stopPreview();
    setView('search');
    setResponse(null);
    setActiveGroup(null);
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
    const key = trackKey(track);
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
    const audio = new Audio(previewStreamUrl(track));
    audioRef.current = audio;
    setPlayer({ key, track, status: 'loading' });
    audio.addEventListener('playing', () => setPlayer({ key, track, status: 'playing' }));
    audio.addEventListener('pause', () =>
      setPlayer((current) => (current?.key === key ? { key, track, status: 'paused' } : current)),
    );
    audio.addEventListener('ended', stopPreview);
    audio.addEventListener('error', stopPreview);
    void audio.play();
  }

  if (view === 'group' && response && activeGroup) {
    const tracks = response.result[activeGroup.key] ?? [];
    return (
      <main className="app-shell">
        <section className="group-screen">
          <header className="group-header">
            <button className="icon-button" type="button" onClick={() => setView('map')} aria-label="뒤로">
              <ArrowLeft size={21} />
            </button>
            <div>
              <h1>{activeGroup.label}</h1>
              <p style={{ color: activeGroup.color }}>총 {tracks.length}곡</p>
            </div>
          </header>

          <div className="view-toggle" role="tablist" aria-label="보기 방식">
            <button
              type="button"
              className={trackViewMode === 'gallery' ? 'selected' : ''}
              onClick={() => setTrackViewMode('gallery')}
              aria-label="갤러리"
            >
              <Grid3X3 size={18} />
            </button>
            <button
              type="button"
              className={trackViewMode === 'list' ? 'selected' : ''}
              onClick={() => setTrackViewMode('list')}
              aria-label="리스트"
            >
              <List size={19} />
            </button>
          </div>

          {tracks.length === 0 ? (
            <div className="empty-message">표시할 추천 곡이 없습니다.</div>
          ) : trackViewMode === 'gallery' ? (
            <div className="track-gallery">
              {tracks.map((track, index) => (
                <TrackGalleryCard
                  key={`${trackKey(track)}-${index}`}
                  track={track}
                  color={activeGroup.color}
                  player={player}
                  onToggle={() => togglePreview(track)}
                />
              ))}
            </div>
          ) : (
            <div className="track-list">
              {tracks.map((track, index) => (
                <TrackListTile
                  key={`${trackKey(track)}-${index}`}
                  track={track}
                  color={activeGroup.color}
                  player={player}
                  onToggle={() => togglePreview(track)}
                />
              ))}
            </div>
          )}
        </section>
        <MiniPlayer player={player} onToggle={togglePreview} onStop={stopPreview} />
      </main>
    );
  }

  if (view === 'map' && response) {
    const mainTrack = buildMainTrack(response, allTracks);
    const tags = buildSearchTags(query || `${response.track_name} ${response.artist}`);

    return (
      <main className="app-shell">
        <section className="result-map-screen">
          <header className="map-header">
            <button className="icon-button" type="button" onClick={backToSearch} aria-label="뒤로">
              <ArrowLeft size={21} />
            </button>
            <SearchTagBar tags={tags} />
          </header>

          <div className="vinyl-map">
            <div className="vinyl-disc map-disc" aria-hidden="true" />
            <Tonearm />
            <MainTrackCard track={mainTrack} />
            {groups.map((group) => (
              <GrooveArc key={`${group.key}-groove`} group={group} count={(response.result[group.key] ?? []).length} />
            ))}
            {groups.map((group) => (
              <GroupNode
                key={group.key}
                group={group}
                tracks={response.result[group.key] ?? []}
                onClick={() => openGroup(group)}
              />
            ))}
          </div>
        </section>
        <MiniPlayer player={player} onToggle={togglePreview} onStop={stopPreview} />
      </main>
    );
  }

  return (
    <main className="app-shell search-shell">
      <section className="home-turntable" aria-label="Side-B 검색">
        <div className="vinyl-disc home-disc" aria-hidden="true" />
        <Tonearm />
        <form className="record-label" onSubmit={submitSearch}>
          <h1>Side-B</h1>
          <p>들리지 않던 쪽으로, 취향의 이면을 넘기다</p>
          <div className="label-search">
            <Search size={18} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="키워드로 음악 탐색"
              disabled={loading}
              aria-label="검색어"
            />
            {query ? (
              <button type="button" onClick={() => setQuery('')} aria-label="검색어 지우기">
                <X size={16} />
              </button>
            ) : (
              <span aria-hidden="true" />
            )}
          </div>
          <button className="label-button" type="submit" disabled={loading}>
            {loading ? <Loader2 className="spin" size={18} /> : null}
            <span>{loading ? '탐색 중' : '탐색 시작'}</span>
          </button>
          {error ? <strong className="search-error">{error}</strong> : null}
        </form>

        {history.map((item, index) => (
          <button
            key={item}
            className={`history-note note-${index + 1}`}
            style={{ background: historyColors[index % historyColors.length] }}
            type="button"
            onClick={() => appendKeyword(item)}
          >
            {item}
          </button>
        ))}
      </section>

      {loading ? <VinylLoadingDialog /> : null}
    </main>
  );
}

function Tonearm() {
  return (
    <svg className="tonearm" viewBox="0 0 220 163" aria-hidden="true" focusable="false">
      <path className="tonearm-shadow" d="M171.6 29.3 Q116.6 55.4 37.4 127.1" />
      <path className="tonearm-metal" d="M171.6 29.3 Q116.6 55.4 37.4 127.1" />
      <path className="tonearm-glint" d="M166.6 25.4 Q113.8 51.8 43.2 121.8" />
      <circle className="tonearm-pivot-outer" cx="171.6" cy="29.3" r="26.4" />
      <circle className="tonearm-pivot-inner" cx="171.6" cy="29.3" r="16.8" />
      <circle className="tonearm-pivot-accent" cx="171.6" cy="29.3" r="6.8" />
      <g className="tonearm-headshell" transform="translate(37.4 127.1) rotate(-16)">
        <rect className="tonearm-shell" x="-20" y="-8" width="40" height="16" rx="5" />
        <circle className="tonearm-stylus" cx="16" cy="8" r="5.8" />
      </g>
    </svg>
  );
}

function SearchTagBar({ tags }: { tags: string[] }) {
  const visible = tags.slice(0, 6);
  const barTags = tags.length > 6 ? [...visible, '...'] : visible;
  return (
    <div className="search-tag-bar">
      {barTags.map((tag, index) => (
        <span key={`${tag}-${index}`}>{tag}</span>
      ))}
    </div>
  );
}

function GrooveArc({ group, count }: { group: GroupData; count: number }) {
  return (
    <span
      className={`groove-neon-arc groove-arc-${group.key}`}
      style={
        {
          '--arc-color': group.color,
          '--arc-weight': `${Math.min(5.8, 2.8 + Math.min(count, 5) * 0.32)}px`,
        } as React.CSSProperties
      }
      aria-hidden="true"
    />
  );
}

function MainTrackCard({ track }: { track: { title: string; artist: string; albumArtUrl?: string | null; hasResult: boolean } }) {
  return (
    <article className="main-track-card">
      <span>Side-B Seed</span>
      <TrackArt url={track.albumArtUrl} color="#7a5e37" className="seed-art" />
      <h2>{track.title}</h2>
      <p>{track.hasResult ? track.artist : '추천 결과를 찾지 못했어요'}</p>
    </article>
  );
}

function GroupNode({
  group,
  tracks,
  onClick,
}: {
  group: GroupData;
  tracks: TrackRecommendation[];
  onClick: () => void;
}) {
  const thumbs = tracks.slice(0, 4);
  const enabled = tracks.length > 0;
  return (
    <button
      className={`group-node ${group.positionClass} ${group.sizeClass}`}
      type="button"
      onClick={onClick}
      disabled={!enabled}
      style={{ '--node-color': group.color } as React.CSSProperties}
    >
      <span className="node-shell" />
      <span className="node-thumbs">
        {[0, 1, 2, 3].map((index) => (
          <TrackThumb key={index} track={thumbs[index]} color={group.color} index={index} />
        ))}
      </span>
      <strong>{tracks.length}</strong>
      <span className="node-copy">
        <b>{group.label}</b>
        <em>{group.description}</em>
      </span>
    </button>
  );
}

function TrackThumb({ track, color, index }: { track?: TrackRecommendation; color: string; index: number }) {
  return (
    <span className={`node-thumb thumb-${index + 1}`} style={{ '--node-color': color } as React.CSSProperties}>
      {track?.album_art_url ? <img src={track.album_art_url} alt="" /> : <Music size={13} />}
    </span>
  );
}

function TrackGalleryCard({
  track,
  color,
  player,
  onToggle,
}: {
  track: TrackRecommendation;
  color: string;
  player: PlayerState | null;
  onToggle: () => void;
}) {
  const active = player?.key === trackKey(track);
  return (
    <article className={`track-card gallery-card ${active ? 'active' : ''}`} style={{ '--track-color': color } as React.CSSProperties}>
      <button className="track-art-button" type="button" onClick={onToggle} aria-label={`${track.name} 미리듣기`}>
        <TrackArt url={track.album_art_url} color={color} />
        <span className="preview-badge">
          <PreviewIcon active={active} status={active ? player.status : 'paused'} />
        </span>
      </button>
      <h2>{track.name}</h2>
      <p>{track.artist}</p>
    </article>
  );
}

function TrackListTile({
  track,
  color,
  player,
  onToggle,
}: {
  track: TrackRecommendation;
  color: string;
  player: PlayerState | null;
  onToggle: () => void;
}) {
  const active = player?.key === trackKey(track);
  return (
    <article className={`track-list-tile ${active ? 'active' : ''}`} style={{ '--track-color': color } as React.CSSProperties}>
      <TrackArt url={track.album_art_url} color={color} />
      <div>
        <h2>{track.name}</h2>
        <p>{track.artist}</p>
      </div>
      <button type="button" onClick={onToggle} aria-label={`${track.name} 미리듣기`}>
        <PreviewIcon active={active} status={active ? player.status : 'paused'} />
      </button>
    </article>
  );
}

function TrackArt({
  url,
  color,
  className = '',
}: {
  url?: string | null;
  color: string;
  className?: string;
}) {
  if (url) {
    return <img className={`track-art-img ${className}`} src={url} alt="" />;
  }
  return (
    <span className={`track-art-fallback ${className}`} style={{ '--track-color': color } as React.CSSProperties}>
      <Music size={18} />
    </span>
  );
}

function PreviewIcon({ active, status }: { active: boolean; status: PlayerStatus }) {
  if (active && status === 'loading') return <Loader2 className="spin" size={19} />;
  if (active && status === 'playing') return <Pause size={20} />;
  return <Play size={20} />;
}

function MiniPlayer({
  player,
  onToggle,
  onStop,
}: {
  player: PlayerState | null;
  onToggle: (track: TrackRecommendation) => void;
  onStop: () => void;
}) {
  if (!player) return null;
  return (
    <aside className="mini-player">
      <TrackArt url={player.track.album_art_url} color="#7cbfb3" />
      <div>
        <strong>{player.track.name}</strong>
        <span>{player.track.artist}</span>
      </div>
      <button type="button" onClick={() => onToggle(player.track)} aria-label="미리듣기 재생 전환">
        <PreviewIcon active status={player.status} />
      </button>
      <button type="button" onClick={onStop} aria-label="미리듣기 정지">
        <X size={18} />
      </button>
    </aside>
  );
}

function VinylLoadingDialog() {
  return (
    <div className="loading-backdrop" role="status" aria-live="polite">
      <div className="loading-dialog">
        <div className="loading-vinyl">
          <div className="loading-disc" />
          <Tonearm />
        </div>
        <div>
          <h2>분석 중</h2>
          <div className="loading-phrases">
            {loadingPhrases.map((phrase, index) => (
              <p key={phrase} style={{ '--phrase-delay': `${index * 2.8}s` } as React.CSSProperties}>
                {phrase}
              </p>
            ))}
          </div>
          <div className="loading-progress" aria-hidden="true" />
        </div>
      </div>
    </div>
  );
}

function buildQuery(keyword: string) {
  const dash = keyword
    .split('-')
    .map((item) => item.trim())
    .filter(Boolean);
  if (dash.length >= 2) return dash.join(' ');

  const comma = keyword
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  if (comma.length >= 2) return comma.join(' ');

  return keyword.trim();
}

function buildSearchTags(keyword: string) {
  const normalized = keyword.trim();
  if (!normalized) return [];
  const comma = normalized
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  if (comma.length > 1) return comma;

  const dash = normalized
    .split('-')
    .map((item) => item.trim())
    .filter(Boolean);
  if (dash.length > 1) return dash;

  return normalized.split(/\s+/).filter(Boolean);
}

function buildMainTrack(response: RecommendResponse, allTracks: TrackRecommendation[]) {
  const title = response.track_name.trim();
  const artist = response.artist.trim();
  let albumArtUrl = response.album_art_url;
  if (!albumArtUrl) {
    const matched = allTracks.find(
      (track) => track.name.trim().toLowerCase() === title.toLowerCase() && track.artist.trim().toLowerCase() === artist.toLowerCase(),
    );
    albumArtUrl = matched?.album_art_url;
  }

  return {
    title: title || 'No Result',
    artist: artist || '아티스트 정보 없음',
    albumArtUrl,
    hasResult: Boolean(title && artist),
  };
}

function trackKey(track: TrackRecommendation) {
  return `${track.artist}::${track.name}`;
}

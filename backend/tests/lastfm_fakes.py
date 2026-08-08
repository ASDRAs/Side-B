"""Last.fm 요청 계층만 흉내내는 test double.

`lastfm_raw` 어댑터는 pylast 객체의 `_request()`가 돌려주는 XML을 직접 판다.
fake가 파싱된 객체를 바로 돌려주면 어댑터의 파서·필드 추출·미제공 처리가 전부
검증에서 빠진다. 그래서 여기서는 Last.fm과 같은 모양의 XML을 만든다.

엔드포인트마다 주는 필드가 다르다는 것도 그대로 재현한다.

- `similartracks`: match, playcount는 있고 listeners는 없다
- `toptracks`: listeners, playcount, rank가 있다
"""

from xml.dom import minidom
from xml.sax.saxutils import escape


def tracks_xml(root_tag: str, rows: list[dict], with_rank: bool = False):
    """Last.fm 응답 형태의 XML 문서를 만든다.

    `<track>` 안의 첫 `<name>`이 곡, `<artist>` 안의 `<name>`이 아티스트다.
    어댑터가 이 순서에 의존하므로 순서를 지킨다.
    """
    nodes = []
    for index, row in enumerate(rows, start=1):
        extra = "".join(
            f"<{key}>{escape(str(value))}</{key}>"
            for key, value in row.items()
            if key not in {"name", "artist"} and value is not None
        )
        rank_attr = f' rank="{index}"' if with_rank else ""
        nodes.append(
            f"<track{rank_attr}>"
            f"<name>{escape(str(row['name']))}</name>"
            f"{extra}"
            f"<artist><name>{escape(str(row['artist']))}</name></artist>"
            f"</track>"
        )
    body = "".join(nodes)
    return minidom.parseString(
        f'<lfm status="ok"><{root_tag}>{body}</{root_tag}></lfm>'
    )


class _Named:
    def __init__(self, name: str):
        self.name = name

    def get_name(self):
        return self.name


class SimilarTrackSource:
    """`track.getSimilar`를 서빙하는 pylast.Track 대역."""

    network = None

    def __init__(self, similar_items, artist="Seed Artist", title="Seed Track"):
        self.similar_items = list(similar_items)
        self.artist = artist
        self.title = title
        self.requests: list[str] = []

    def get_name(self):
        return self.title

    def get_artist(self):
        return _Named(self.artist)

    def _get_params(self):
        return {"artist": self.artist, "track": self.title}

    def _request(self, method, cacheable=True, params=None):
        self.requests.append(method)
        limit = int((params or {}).get("limit") or len(self.similar_items))
        rows = [
            {
                "name": item.item.get_name(),
                "artist": item.item.get_artist().get_name(),
                "match": item.match,
                "playcount": getattr(item, "playcount", 1000),
            }
            for item in self.similar_items[:limit]
        ]
        return tracks_xml("similartracks", rows)


class ArtistTopTracksSource:
    """`artist.getTopTracks`를 서빙하는 pylast.Artist 대역."""

    network = None

    def __init__(self, name, titles, listeners=None):
        self.name = name
        self.titles = list(titles)
        self.listeners = listeners
        self.requests: list[str] = []

    def get_name(self):
        return self.name

    def _get_params(self):
        return {"artist": self.name}

    def _request(self, method, cacheable=True, params=None):
        self.requests.append(method)
        limit = int((params or {}).get("limit") or len(self.titles))
        rows = [
            {
                "name": title,
                "artist": self.name,
                "playcount": 10_000 - index * 100,
                "listeners": (
                    self.listeners[index]
                    if self.listeners is not None
                    else 1_000 - index * 10
                ),
            }
            for index, title in enumerate(self.titles[:limit])
        ]
        return tracks_xml("toptracks", rows, with_rank=True)

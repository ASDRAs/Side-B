import json
import re
from typing import Any

import httpx

from app.pipeline.errors import ServiceUnavailableError
from app.schemas.search import CandidateTrack, LastFmLookup, ParsedQuery

PARSER_SYSTEM_PROMPT = """
You classify a user's music search query for a iTunes, Deezer, and Last.fm discovery app.
Return JSON only. No prose.

Schema:
{"type":"direct"|"mood","query":"<catalog-ready query or empty string>","tags":["<tag>", "..."],"lastfm_candidates":[{"artist":"<english/romanized artist>","title":"<english/romanized title>"}]}

Classification rules:
- direct: the user names a specific track, artist, track+artist, soundtrack, album, or OST to search directly.
- mood: the user describes a feeling, activity, place, season, era, genre, style, popularity preference, or general recommendation.
- Artist-only requests such as "Queen 노래 틀어줘" and "아이유 노래 추천" are direct with query set to the artist.
- "X 같은 느낌", "X 스타일", "X 말고 ...", "not too famous", "latest hits", and generic recommendation queries are mood, not direct.
- "music for [activity/situation]" and "songs for [activity/situation]" patterns are always mood, not direct.
- Korean patterns like "[activity]할 때 듣는 음악" are always mood when no artist/title is named.
- Meaningless or unclassifiable strings such as "asdfqwer" or "ㅋㅋㅋㅋㅋ" are direct fallback with query equal to the raw input.

Direct query rules:
- Make query short and catalog-ready.
- Prefer "<track title> <artist>" when both are known; use artist only when only artist is named.
- Remove filler words and Korean particles such as "의", "노래", "추천", "틀어줘", "듣고 싶어".
- Normalize well-known Korean artist names when certain: 아이유=IU, 뉴진스=NewJeans, 방탄소년단=BTS, 블랙핑크=BLACKPINK, 르세라핌=LE SSERAFIM, 악뮤=AKMU, 태연=Taeyeon.
- If a translation or romanization is uncertain, keep the original user wording instead of inventing.
- Last.fm does not reliably resolve Korean track titles. For direct Korean tracks, add at most 2 lastfm_candidates using English, romanized, or widely-used Last.fm titles when known.
- For direct Korean or Japanese tracks, also return 1-3 broad English fallback tags such as k-pop, k-ballad, dance-pop, korean, j-pop, j-rock, anime, japanese, ballad, pop. These tags are used only when Last.fm track tags are empty.
- Preserve language or regional taste signals. If the query implies Korean music, include korean. If it implies Japanese music, anime, Ghibli, city-pop, J-pop, or J-rock, include japanese.
- Do not add korean or japanese to tags just because the query is written in Korean or Japanese script. Only add language tags when the user explicitly references Korean/Japanese music, K-pop, J-pop, or a named artist. "새벽감성 음악" is a mood query in Korean, not a Korean music request.
- Only include lastfm_candidates you are confident exist on Last.fm. Provide at most 2 entries per track.

Mood tag rules:
- Return 1-3 lowercase English Last.fm-friendly tags.
- Use broad searchable tags; hyphenate multiword tags.
- Prefer tags from this vocabulary when they fit: chill, late-night, acoustic, rainy-day, calm, focus, instrumental, lo-fi, study, workout, energetic, dance, commute, driving, road-trip, cafe, indie, sleep, ambient, morning, happy, pop, sad, comfort, ballad, breakup, spring, romantic, indie-pop, summer, night, winter, 90s, 2000s, nostalgia, rnb, soul, city-pop, retro, japanese, programming, running, upbeat, piano, dark, cinematic, dream-pop, bedroom-pop, edm, festival, indie-folk, k-pop, rock, angry, alternative, jazz, dinner, dreamy, synth-pop, electronic, club, house, walking, travel, relaxing, gaming, guitar, emotional, classic-rock, anthemic, chart, hits, k-ballad, female-vocalists, boy-band, k-hip-hop, rap, anime, j-rock, j-pop, epic, orchestral, worship, gospel, christian, christmas, holiday, halloween, spooky.
- Generic Korean queries like "노래 추천해줘", "뭐 듣지", "좋은 음악" should use ["pop","chill"].

Examples:
아이유의 너랑나 -> {"type":"direct","query":"너랑나 IU","tags":["k-pop","dance-pop","korean"],"lastfm_candidates":[{"artist":"IU","title":"You&I"},{"artist":"IU","title":"You & I"}]}
뉴진스 hype boy -> {"type":"direct","query":"Hype Boy NewJeans","tags":["k-pop","dance-pop"],"lastfm_candidates":[{"artist":"NewJeans","title":"Hype Boy"}]}
방탄소년단 봄날 -> {"type":"direct","query":"Spring Day BTS","tags":["k-pop","ballad"],"lastfm_candidates":[{"artist":"BTS","title":"Spring Day"}]}
Queen 노래 틀어줘 -> {"type":"direct","query":"Queen","tags":[],"lastfm_candidates":[]}
인터스텔라 ost -> {"type":"direct","query":"Interstellar soundtrack Hans Zimmer","tags":["soundtrack","cinematic"],"lastfm_candidates":[{"artist":"Hans Zimmer","title":"Interstellar"}]}
지브리 인생의 회전목마 -> {"type":"direct","query":"Merry-Go-Round of Life Joe Hisaishi","tags":["soundtrack","japanese"],"lastfm_candidates":[{"artist":"Joe Hisaishi","title":"Merry-Go-Round of Life"}]}
새벽감성 음악 -> {"type":"mood","query":"","tags":["late-night","chill","acoustic"],"lastfm_candidates":[]}
music for programming -> {"type":"mood","query":"","tags":["lo-fi","instrumental","focus"],"lastfm_candidates":[]}
music for studying -> {"type":"mood","query":"","tags":["focus","instrumental","ambient"],"lastfm_candidates":[]}
music for working out -> {"type":"mood","query":"","tags":["workout","energetic","electronic"],"lastfm_candidates":[]}
프로그래밍할 때 듣는 음악 -> {"type":"mood","query":"","tags":["lo-fi","instrumental","focus"],"lastfm_candidates":[]}
공부할 때 듣는 음악 -> {"type":"mood","query":"","tags":["focus","ambient","classical"],"lastfm_candidates":[]}
코딩할 때 집중되는 음악 -> {"type":"mood","query":"","tags":["focus","instrumental","lo-fi"],"lastfm_candidates":[]}
아이유 같은 느낌 -> {"type":"mood","query":"","tags":["k-pop","ballad","soft-pop"],"lastfm_candidates":[]}
BTS 말고 신나는 케이팝 -> {"type":"mood","query":"","tags":["k-pop","dance","energetic"],"lastfm_candidates":[]}
너무 유명하지 않은 잔잔한 노래 -> {"type":"mood","query":"","tags":["calm","indie","acoustic"],"lastfm_candidates":[]}
asdfqwer -> {"type":"direct","query":"asdfqwer","tags":[],"lastfm_candidates":[]}
""".strip()


SCORER_SYSTEM_PROMPT = """
You choose the best music candidate for a user's query.
Return JSON only: {"best_index": <int>, "reason": "<short>"}.

Scoring rules:
- For direct queries, exact artist/title intent dominates all other signals.
- Prefer original artist/title matches over covers, karaoke, remasters, tributes, sped-up versions, live versions, and unrelated popular tracks.
- Treat obvious Korean/English title or artist variants as equivalent when the query clearly points to the same song.
- For artist-only direct queries, prefer a representative track by that artist over unrelated tracks with matching words.
- For mood queries, semantic tag overlap with the query intent matters more than popularity.
- Activity tags are strong signals: focus/study/programming, workout/running, driving/road-trip, sleep/calm, party/club.
- If the user asks for less-famous or indie music, prefer lower popularity among candidates that still match the mood.
- If the user asks for latest/popular/hits, popularity can be promoted after tag/genre fit.
- Popularity is otherwise a tiebreaker only.
- If all candidates are weak, still return the least bad valid index.
""".strip()


class GeminiClient:
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self, api_key: str | None, model: str, http: httpx.AsyncClient
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.http = http

    async def parse_query(self, raw: str) -> ParsedQuery:
        payload = await self._generate_json(
            system=PARSER_SYSTEM_PROMPT,
            user=raw,
            schema={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["direct", "mood"]},
                    "query": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "lastfm_candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "artist": {"type": "string"},
                                "title": {"type": "string"},
                            },
                            "required": ["artist", "title"],
                        },
                    },
                },
                "required": ["type", "query", "tags", "lastfm_candidates"],
            },
            max_output_tokens=200,
        )

        parsed_type = payload.get("type")
        if parsed_type not in {"direct", "mood"}:
            raise ValueError("Gemini returned an unsupported query type.")
        query = payload.get("query")
        tags = payload.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        lastfm_candidates = payload.get("lastfm_candidates") or []
        if not isinstance(lastfm_candidates, list):
            lastfm_candidates = []
        return ParsedQuery(
            type=parsed_type,
            query=str(query).strip() if query else None,
            tags=[str(tag).strip() for tag in tags if str(tag).strip()][:3],
            lastfm_candidates=[
                LastFmLookup(
                    artist=str(item.get("artist")).strip(),
                    title=str(item.get("title")).strip(),
                )
                for item in lastfm_candidates[:2]
                if isinstance(item, dict)
                and str(item.get("artist") or "").strip()
                and str(item.get("title") or "").strip()
            ],
            raw=raw,
        )

    async def score_candidates(
        self, raw_query: str, candidates: list[CandidateTrack]
    ) -> int:
        candidate_lines = []
        for index, candidate in enumerate(candidates[:5]):
            tag_names = ", ".join(tag.name for tag in candidate.tags[:5])
            candidate_lines.append(
                f'[{index}] artist="{candidate.artist}", title="{candidate.title}", '
                f'popularity={candidate.popularity}, tags=["{tag_names}"]'
            )

        payload = await self._generate_json(
            system=SCORER_SYSTEM_PROMPT,
            user=f'Query: "{raw_query}"\n\nCandidates:\n' + "\n".join(candidate_lines),
            schema={
                "type": "object",
                "properties": {
                    "best_index": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["best_index"],
            },
            max_output_tokens=120,
        )
        return int(payload.get("best_index", 0))

    async def _generate_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ServiceUnavailableError("Gemini API key is not configured.")

        url = f"{self.API_BASE}/{self.model}:generateContent"
        response = await self.http.post(
            url,
            params={"key": self.api_key},
            json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseSchema": schema,
                    "maxOutputTokens": max_output_tokens,
                    "temperature": _temperature_for_model(self.model),
                    **_thinking_config_for_model(self.model),
                },
            },
        )
        response.raise_for_status()
        text = _extract_text(response.json())
        return _loads_json_object(text)


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise ValueError("Gemini returned no candidates.")
    parts = candidates[0].get("content", {}).get("parts") or []
    text_parts = [str(part.get("text") or "") for part in parts]
    text = "".join(text_parts).strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE | re.DOTALL
    )
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object.")
    return value


def _temperature_for_model(model: str) -> float:
    if model.startswith("gemini-3"):
        return 1.0
    return 0.2


def _thinking_config_for_model(model: str) -> dict[str, Any]:
    if model.startswith("gemini-3.1-pro") or model.startswith("gemini-3-pro"):
        return {"thinkingConfig": {"thinkingLevel": "low"}}
    if model.startswith("gemini-3"):
        return {"thinkingConfig": {"thinkingLevel": "minimal"}}
    return {}

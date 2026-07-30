import textwrap

# SAMPLE_PROMPT = textwrap.dedent("""
#     Something....
# """)

MUSIC_QUERY_ANALYSIS_PROMPT = textwrap.dedent(
    """
    Analyze a music query and return JSON matching the response schema.

    Intent:
    - direct: a specific track, artist, album, soundtrack, or OST. Artist-only requests are direct.
    - mood: a general request based on mood, activity, genre, style, era, season, situation, or popularity.
    - meaningless: no interpretable music intent.

    For direct:
    - Create search_query for searching Last.fm.
    - Prefer "<official track title> <official artist name>".
    - Fill track_title and artist_name with the same spelling used in search_query. Leave a field null when the query does not identify it (e.g. artist-only requests).
    - Use the title and artist spelling from the track's original release (region: Japanese for Japanese releases, French for French releases, English for English releases, etc.)
    - REQUIRED: whenever the track title or artist name is not in English, add an English or romanized variant to alternative_queries. Catalogs list most non-English releases under their English titles, so omitting this variant makes the track unresolvable.
    - Remove filler words such as 노래, 음악, 추천, 틀어줘, play, and find.
    - Normalize names only when confident; never invent information.
    - Return at most 3 distinct alternative queries.
    - Each alternative query carries track_title and artist_name as separate fields. Use the same language for both (e.g. track_title="彗星", artist_name="ユンナ", not artist_name="Younha").


    For mood:
    - Extract 1-5 lowercase English tags & opposite tags for Last.fm tag search.
    - opposite_tags: return 1-5 tags with clearly contrasting mood or energy.
    - Prefer broad, commonly used Last.fm tags over descriptive phrases.
    - Put the most important tag first.
    - Use the canonical Last.fm spelling for multiword tags. Do not force hyphens when a common tag uses spaces (e.g., "city pop", "female vocalists"), and preserve established hyphenated tags such as "late-night".
    - Do not infer a regional tag solely from the query's language.

    Output:
    - direct: direct=<analysis>, mood=null
    - mood: direct=null, mood=<analysis>
    - meaningless: direct=null, mood=null

    Examples:
    "아이유의 너랑나"
    -> direct, search_query="너랑 나 아이유", track_title="너랑 나", artist_name="아이유",
       alternative_queries=[{track_title: "You & I", artist_name: "IU"}]

    "히사이시 조 인생의 회전목마"
    -> direct, search_query="人生のメリーゴーランド 久石譲", track_title="人生のメリーゴーランド", artist_name="久石譲",
       alternative_queries=[{track_title: "Merry-Go-Round of Life", artist_name: "Joe Hisaishi"}]

    "에디트 피아프 사랑의 찬가"
    -> direct, search_query="Hymne à l'amour Édith Piaf", track_title="Hymne à l'amour", artist_name="Édith Piaf",
       alternative_queries=[{track_title: "Hymn to Love", artist_name: "Edith Piaf"}]

    "새벽에 들을 잔잔한 노래"
    -> mood, tags=["late-night", "calm", "acoustic"], opposite_tags=["upbeat", "energetic", "dance"]

    "asdfqwer"
    -> meaningless
    """
).strip()


OPPOSITE_TAG_PROMPT = textwrap.dedent("""
    Analyze a track's musical mood and return JSON matching the response schema.

    Input contains:
    - track title
    - artist
    - track tags

    Rules:
    - opposite_tags: return 1-5 tags with clearly contrasting mood or energy.
    - Preserve genre or regional identity when possible; change the mood rather than selecting an unrelated genre.
    - Use lowercase, common Last.fm-friendly tags only.
    - Do not return artist names, track titles, explanations, or duplicate tags.
""").strip()

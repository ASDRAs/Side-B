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
    - Use the title and artist spelling from the track's original release (region: Japanese for Japanese releases, French for French releases, English for English releases, etc.)
    - If the official native spelling may not search well on Last.fm, add English or romanized variants to alternative_queries.
    - Remove filler words such as 노래, 음악, 추천, 틀어줘, play, and find.
    - Normalize names only when confident; never invent information.
    - Return at most 3 distinct alternative queries.

    For mood:
    - Extract 1-5 lowercase English tags for Last.fm tag search.
    - Prefer broad, commonly used Last.fm tags over descriptive phrases.
    - Put the most important tag first.
    - Use hyphens for multiword tags.
    - Do not infer a regional tag solely from the query's language.

    Output:
    - direct: direct=<analysis>, mood=null
    - mood: direct=null, mood=<analysis>
    - meaningless: direct=null, mood=null

    Examples:
    "아이유의 너랑나"
    -> direct, search_query="너랑 나 IU", alternative_queries=["You & I IU"]

    "히사이시 조 인생의 회전목마"
    -> direct, search_query="人生のメリーゴーランド 久石譲", alternative_queries=["Merry-Go-Round of Life Joe Hisaishi"]

    "에디트 피아프 사랑의 찬가"
    -> direct, search_query="Hymne à l'amour Édith Piaf", alternative_queries=["Hymn to Love Edith Piaf"]

    "새벽에 들을 잔잔한 노래"
    -> mood, tags=["late-night", "calm", "acoustic"]

    "asdfqwer"
    -> meaningless
    """
).strip()

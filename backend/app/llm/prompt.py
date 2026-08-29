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

TRACK_SEARCH_ANALYSIS_PROMPT = textwrap.dedent(
   """
   Analyze the given track title and artist, then return JSON matching
   the response schema.

   Country:
   - Return "korea" if the original track is primarily released by a
   South Korean artist or group.
   - Otherwise return "foreign".
   - Do not determine country only from the input language.

   Search queries:
   - Return 1-3 distinct search queries in priority order.
   - Each query must contain track_title and artist_name separately.
   - The first query should use the most likely official title and
   official artist name.
   - Additional queries may use an English title, romanized spelling,
   original-language spelling, stage name, or commonly used catalog spelling.
   - Use the same language or writing system for the title and artist
   in each alternative whenever possible.
   - Keep the original release language when confidently known.
   - Include an English or romanized query for non-English releases
   when a commonly used spelling exists.

   Cleaning:
   - Remove non-identifying text such as:
   official video, official audio, music video, mv, m/v, lyrics,
   lyric video, audio, visualizer, teaser, topic, hd, 4k, reaction,
   full version, 노래, 음악, 뮤직비디오, 가사, 공식 영상.
   - Remove channel names, uploader text, unnecessary punctuation,
   hashtags, emojis, quotation marks, and duplicated artist names.
   - Remove album names and release years when they are not part of
   the official track title.
   - Preserve meaningful version information such as live, remix,
   acoustic, instrumental, or remastered only when the input
   explicitly requests that specific version.
   - Preserve featured artists only when they are part of the actual release.
   - Do not change the track to another song.
   - Do not invent titles, translations, or artist names when uncertain.

   Examples:

   Input:
   track_title="[Official MV] 아이유(IU) - 밤편지 가사"
   artist="IU Official"
   Output:
   country="korea"
   search_queries=[
   {"track_title": "밤편지", "artist_name": "아이유"},
   {"track_title": "Through the Night", "artist_name": "IU"}
   ]

   Input:
   track_title="YOASOBI「アイドル」Official Music Video"
   artist="Ayase / YOASOBI"
   Output:
   country="foreign"
   search_queries=[
   {"track_title": "アイドル", "artist_name": "YOASOBI"},
   {"track_title": "Idol", "artist_name": "YOASOBI"}
   ]

   Input:
   track_title="NewJeans (뉴진스) 'Hype Boy' Official MV"
   artist="HYBE LABELS"
   Output:
   country="korea"
   search_queries=[
   {"track_title": "Hype Boy", "artist_name": "NewJeans"},
   {"track_title": "Hype Boy", "artist_name": "뉴진스"}
   ]
   """
).strip()
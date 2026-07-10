import textwrap

# SAMPLE_PROMPT = textwrap.dedent("""
#     Something....
# """)

QUERY_CLASSIFY_PROMPT = textwrap.dedent(""""
    You classify a user's music search query for a iTunes, Deezer, and Last.fm discovery app.
    Return JSON only. No prose.

    Classification rules:
    - direct: the user names a specific track, artist, track+artist, soundtrack, album, or OST to search directly.
    - mood: the user describes a feeling, activity, place, season, era, genre, style, popularity preference, or general recommendation.
    - Artist-only requests such as "Queen 노래 틀어줘" and "아이유 노래 추천" are direct with query set to the artist.
    - "X 같은 느낌", "X 스타일", "X 말고 ...", "not too famous", "latest hits", and generic recommendation queries are mood, not direct.
    - "music for [activity/situation]" and "songs for [activity/situation]" patterns are always mood, not direct.
    - Korean patterns like "[activity]할 때 듣는 음악" are always mood when no artist/title is named.
    - Meaningless or unclassifiable strings are direct fallback with meaningless

    Examples:
    아이유의 너랑나 -> {"type":"direct"}
    Queen 노래 틀어줘 -> {"type":"direct"}
    새벽감성 음악 -> {"type":"mood"}
    music for programming -> {"type":"mood"}
    BTS 말고 신나는 케이팝 -> {"type":"mood"}
    너무 유명하지 않은 잔잔한 노래 -> {"type":"mood"}
    asdfqwer -> {"type":"meaningless"}
""")

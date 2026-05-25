from app.services.llm import PARSER_SYSTEM_PROMPT, SCORER_SYSTEM_PROMPT


def test_parser_prompt_encodes_verified_direct_examples():
    assert "아이유의 너랑나" in PARSER_SYSTEM_PROMPT
    assert '"query":"너랑나 IU"' in PARSER_SYSTEM_PROMPT
    assert "lastfm_candidates" in PARSER_SYSTEM_PROMPT
    assert '"title":"You&I"' in PARSER_SYSTEM_PROMPT
    assert "뉴진스 hype boy" in PARSER_SYSTEM_PROMPT
    assert "방탄소년단 봄날" in PARSER_SYSTEM_PROMPT
    assert '"title":"Spring Day"' in PARSER_SYSTEM_PROMPT
    assert "Queen 노래 틀어줘" in PARSER_SYSTEM_PROMPT
    assert "인터스텔라 ost" in PARSER_SYSTEM_PROMPT
    assert "지브리 인생의 회전목마" in PARSER_SYSTEM_PROMPT


def test_parser_prompt_encodes_verified_mood_policy():
    assert '"X 같은 느낌"' in PARSER_SYSTEM_PROMPT
    assert '"X 말고 ..."' in PARSER_SYSTEM_PROMPT
    assert "music for programming" in PARSER_SYSTEM_PROMPT
    assert '"query":"","tags":["lo-fi","instrumental","focus"]' in PARSER_SYSTEM_PROMPT
    assert "always mood" in PARSER_SYSTEM_PROMPT
    assert "not a Korean music request" in PARSER_SYSTEM_PROMPT
    assert "너무 유명하지 않은 잔잔한 노래" in PARSER_SYSTEM_PROMPT
    assert '"노래 추천해줘"' in PARSER_SYSTEM_PROMPT
    assert '["pop","chill"]' in PARSER_SYSTEM_PROMPT


def test_parser_prompt_keeps_tags_lastfm_friendly():
    for tag in [
        "late-night",
        "focus",
        "instrumental",
        "k-pop",
        "city-pop",
        "female-vocalists",
    ]:
        assert tag in PARSER_SYSTEM_PROMPT
    assert "japanese" in PARSER_SYSTEM_PROMPT
    assert "korean" in PARSER_SYSTEM_PROMPT


def test_scorer_prompt_encodes_selection_priorities():
    assert "exact artist/title" in SCORER_SYSTEM_PROMPT
    assert "covers, karaoke" in SCORER_SYSTEM_PROMPT
    assert "semantic tag overlap" in SCORER_SYSTEM_PROMPT
    assert "less-famous or indie" in SCORER_SYSTEM_PROMPT
    assert "Popularity is otherwise a tiebreaker only" in SCORER_SYSTEM_PROMPT

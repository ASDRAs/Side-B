MOOD_QUERY_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("감성", "감성적", "emotional"), ["emotional", "chill"]),
    (("잔잔", "차분", "calm"), ["calm", "chill"]),
    (("신나는", "신나", "energetic", "upbeat"), ["upbeat", "dance"]),
    (("새벽", "밤", "late night"), ["late-night", "chill"]),
    (("몽환", "dreamy"), ["dreamy", "synth-pop"]),
    (("집중", "공부", "코딩", "focus", "study"), ["focus", "instrumental"]),
)

SIMPLE_TAG_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("study", "studying", "programming", "coding", "work", "집중", "공부", "코딩", "프로그래밍"), ["focus", "instrumental", "lo-fi"]),
    (("workout", "run", "running", "gym", "exercise", "운동", "헬스", "러닝"), ["workout", "energetic", "dance"]),
    (("drive", "driving", "road", "드라이브", "운전"), ["road-trip", "indie", "feel-good"]),
    (("commute", "출근", "퇴근"), ["commute", "chill", "pop"]),
    (("sleep", "relax", "relaxing", "잠", "수면", "휴식"), ["sleep", "ambient", "calm"]),
    (("새벽", "chill", "calm", "잔잔"), ["late-night", "chill", "acoustic"]),
    (("sad", "슬픈", "우울", "이별"), ["sad", "ballad", "emotional"]),
    (("happy", "신나는", "기분좋", "파티"), ["happy", "upbeat", "pop"]),
)

ACTIVITY_MOOD_RULES: tuple[tuple[tuple[str, ...], list[str]], ...] = (
    (("프로그래밍", "코딩"), ["lo-fi", "instrumental", "focus"]),
    (("공부",), ["focus", "instrumental", "ambient"]),
    (("운동", "헬스", "러닝"), ["workout", "energetic", "dance"]),
    (("드라이브", "운전"), ["road-trip", "indie", "feel-good"]),
    (("출근", "퇴근"), ["commute", "chill", "pop"]),
    (("새벽",), ["late-night", "chill", "acoustic"]),
    (("비 오는", "비오는", "비 오", "장마", "빗소리"), ["rainy-day", "chill", "acoustic"]),
    (("카페", "작업"), ["cafe", "acoustic", "chill"]),
)

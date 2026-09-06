"""추론 이미지 없이 돌릴 수 있는 정적 검사.

pipeline은 numpy/torch를 import하므로 백엔드 환경에서 실행할 수 없다.
지켜야 할 불변식은 "디코딩 창 == 분석 창"이고, 그건 소스에서 확인 가능하다.
"""

import ast
from pathlib import Path

MODULE = ast.parse(
    (Path(__file__).resolve().parents[1] / "pipeline.py").read_text(encoding="utf-8")
)
WINDOW = "ANALYSIS_SECONDS"


def _function(name):
    return next(
        node
        for node in ast.walk(MODULE)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_ffmpeg_decodes_exactly_the_window_the_model_analyses():
    arguments = [
        node
        for node in ast.walk(_function("decode_audio"))
        if isinstance(node, ast.List)
    ][0].elts
    flag = next(
        index
        for index, node in enumerate(arguments)
        if isinstance(node, ast.Constant) and node.value == "-t"
    )
    duration = arguments[flag + 1]
    assert isinstance(duration, ast.Call)
    assert duration.func.id == "str"
    assert duration.args[0].id == WINDOW

    split = next(
        node
        for node in ast.walk(_function("_predict"))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "split_audio"
    )
    assert split.args[2].id == WINDOW


def test_a_long_preview_is_truncated_rather_than_rejected():
    # 길이를 이유로 InvalidAudio를 던지면 모델이 보지도 않는 구간 때문에
    # 30초 넘는 미리듣기가 422가 된다.
    body = _function("decode_audio")
    lengths = [
        node
        for node in ast.walk(body)
        if isinstance(node, ast.Compare)
        and any(
            isinstance(inner, ast.Name) and inner.id == "SAMPLE_RATE"
            for operand in [node.left, *node.comparators]
            for inner in ast.walk(operand)
        )
    ]
    assert lengths == []

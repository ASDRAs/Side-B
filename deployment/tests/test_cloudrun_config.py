import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_edge_deadline_exceeds_actual_router_deadline():
    config = json.loads((ROOT / "deployment/cloudrun.json").read_text())
    router = ast.parse(
        (ROOT / "backend/app/routers/genre_classification.py").read_text(
            encoding="utf-8"
        )
    )
    deadlines = [
        ast.literal_eval(keyword.value)
        for node in ast.walk(router)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
        and node.func.attr == "wait_for"
        for keyword in node.keywords
        if keyword.arg == "timeout"
    ]
    assert len(deadlines) == 1
    assert config["backend"]["timeout_seconds"] >= deadlines[0] + 30
    assert config["backend"]["inference_timeout_seconds"] < deadlines[0]


def test_inference_capacity_remains_bounded():
    config = json.loads((ROOT / "deployment/cloudrun.json").read_text())
    assert config["inference"]["min_instances"] == 0
    assert config["inference"]["max_instances"] == 1
    assert config["inference"]["concurrency"] == 1
    assert (
        config["inference"]["timeout_seconds"]
        > config["backend"]["inference_timeout_seconds"]
    )

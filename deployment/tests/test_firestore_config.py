import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIRESTORE = ROOT / "deployment/firestore"


def test_client_rules_deny_every_document():
    rules = (FIRESTORE / "firestore.rules").read_text(encoding="utf-8")
    code = re.sub(r"//.*", "", rules)
    allows = re.findall(r"allow\s+([^:;]+):\s*if\s+([^;]+);", code)

    assert "match /{document=**}" in code
    assert allows == [("read, write", "false")]


def test_list_query_index_matches_the_store_query():
    indexes = json.loads((FIRESTORE / "firestore.indexes.json").read_text())["indexes"]
    store = (ROOT / "backend/app/services/access.py").read_text(encoding="utf-8")

    assert {
        "collectionGroup": "access_users",
        "queryScope": "COLLECTION",
        "fields": [
            {"fieldPath": "status", "order": "ASCENDING"},
            {"fieldPath": "requested_at", "order": "ASCENDING"},
        ],
    } in indexes
    assert 'FieldFilter("status", "==", status)' in store
    assert '.order_by("requested_at")' in store


def test_emulator_config_points_at_the_repository_rules():
    config = json.loads((FIRESTORE / "firebase.json").read_text())

    assert config["firestore"] == {
        "rules": "firestore.rules",
        "indexes": "firestore.indexes.json",
    }
    assert config["emulators"]["firestore"]["host"] == "127.0.0.1"

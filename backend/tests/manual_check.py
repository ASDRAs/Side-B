import asyncio
import json
import os
import sys

import httpx

MUST_PASS = [
    (
        "아이유의 너랑나",
        lambda r: (
            ("IU" in r["artist"] or "아이유" in r["artist"])
            and any(
                x in r["title"].lower()
                for x in ["너랑나", "you&i", "you & i", "you and i"]
            )
        ),
    ),
    (
        "music for programming",
        lambda r: (
            r["tags"]
            and any(
                t["name"] in ["lo-fi", "instrumental", "focus", "ambient"]
                for t in r["tags"]
            )
        ),
    ),
    ("새벽감성 음악", lambda r: "korean" not in [t["name"] for t in r["tags"]]),
    ("Bohemian Rhapsody Queen", lambda r: "Queen" in r["artist"]),
    (
        "프로그래밍할 때 듣는 음악",
        lambda r: (
            r["tags"]
            and any(t["name"] in ["lo-fi", "focus", "instrumental"] for t in r["tags"])
        ),
    ),
    ("새벽에 혼자 듣는 음악", lambda r: "Harry Styles" not in r["artist"]),
]

OPTIONAL = [
    "asdfqwer",
]


def default_base_url() -> str:
    return "http://localhost:8000"


async def main() -> None:
    base_url = os.getenv("SEARCH_BASE_URL", default_base_url()).rstrip("/")
    failures = []
    async with httpx.AsyncClient(timeout=12.0) as http:
        for query, validator in MUST_PASS:
            response = await http.post(f"{base_url}/search", json={"query": query})
            print(f"{query}: {response.status_code}")
            payload = response.json()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            if response.status_code != 200 or not validator(payload):
                failures.append(query)

        for query in OPTIONAL:
            response = await http.post(f"{base_url}/search", json={"query": query})
            print(f"{query}: {response.status_code}")
            print(json.dumps(response.json(), ensure_ascii=False, indent=2))

    if failures:
        print("FAILED:", ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

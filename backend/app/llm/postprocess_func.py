import json


def json_parser(text: str) -> dict:
    """
    Output이 json으로 넘어왔는지 검사하는 func
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON format", "raw": text}

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMResponseParseError(Exception):
    pass


def parse_json_response(raw: str) -> Any:
    """LLMs asked for "only JSON" still sometimes wrap it in a markdown code
    fence — strip that before parsing rather than treating it as malformed."""
    cleaned = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LLMResponseParseError(f"Could not parse JSON from LLM response: {raw[:500]}") from e

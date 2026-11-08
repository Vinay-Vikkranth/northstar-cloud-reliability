import re
from typing import Any

_SECRET_KEY = re.compile(r"(token|secret|password|authorization|api[_-]?key|cookie|credential)", re.I)
def redact_text(value: str) -> str:
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(\S+(?:\s+\S+)?)", r"\1[redacted]", value)
    value = re.sub(r"(?i)\b(bearer)\s+\S+", r"\1 [redacted]", value)
    value = re.sub(r"(?i)\b(token|password|secret|api[_-]?key)\b(\s*[:=]\s*)(\S+)", r"\1\2[redacted]", value)
    return value


def sanitize(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        return "[truncated]"
    if isinstance(value, str):
        text = redact_text(value)
        return text if len(text) <= 2000 else text[:2000] + "…[truncated]"
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _SECRET_KEY.search(str(key)):
                cleaned[str(key)] = "[redacted]"
            else:
                cleaned[str(key)] = sanitize(item, depth=depth + 1)
        return cleaned
    if isinstance(value, list):
        return [sanitize(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize(str(value), depth=depth + 1)

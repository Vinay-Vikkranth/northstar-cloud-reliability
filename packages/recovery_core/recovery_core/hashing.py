import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def fingerprint_record(*, record_id: str, plan: str | None, revision: object, modified_at: str | None) -> str:
    return canonical_hash(
        {
            "id": record_id,
            "plan": plan,
            "revision": revision,
            "modified_at": modified_at,
        }
    )

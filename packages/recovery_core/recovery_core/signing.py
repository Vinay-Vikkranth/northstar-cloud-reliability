import hashlib
import hmac


def sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def signatures_match(secret: str, body: bytes, header: str | None) -> bool:
    if not header:
        return False
    expected = sign_body(secret, body)
    return hmac.compare_digest(expected, header.strip())

def retry_delay_seconds(retry_after: str | None, *, attempt: int, cap: float = 30.0) -> float:
    """Honor Retry-After, with a short floor when the header is missing or zero.

    A zero wait would turn a 429 into a tight loop. Positive Retry-After values are
    used as given, up to cap seconds. This delay is for reviewed or bounded retries,
    not an unbounded client loop.
    """
    parsed: float | None = None
    if retry_after:
        try:
            parsed = float(retry_after.strip())
        except ValueError:
            parsed = None
    if parsed is None or parsed <= 0:
        return min(cap, 0.5 * attempt)
    return min(cap, parsed)

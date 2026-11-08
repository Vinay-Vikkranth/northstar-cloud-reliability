"""Logging redaction must not break numeric format placeholders."""

import io
import logging
import json

from recovery_api.telemetry import JsonLogFormatter, RedactingFilter


def _logger() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.redacting-filter")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, stream


def test_numeric_placeholders_stay_valid_and_secrets_are_redacted():
    logger, stream = _logger()
    logger.info('HTTP Request: %s %s "%s %d %s"', "GET", "http://127.0.0.1/v1", "HTTP/1.1", 200, "OK")
    logger.info("Authorization: Bearer %s", "super-secret-token")
    logger.info("password=hunter2")
    text = stream.getvalue()
    assert 'HTTP Request: GET http://127.0.0.1/v1 "HTTP/1.1 200 OK"' in text
    assert "super-secret-token" not in text
    assert "hunter2" not in text
    assert "[redacted]" in text


def test_json_log_escapes_quotes_newlines_and_exception_secrets():
    logger, stream = _logger()
    logger.handlers[0].setFormatter(JsonLogFormatter())
    logger.info('HTTP "200 OK"\nsecond line')
    try:
        raise ValueError("password=hunter2")
    except ValueError:
        logger.exception("Request failed")
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == 'HTTP "200 OK"\nsecond line'
    assert "hunter2" not in json.loads(lines[1])["exception"]

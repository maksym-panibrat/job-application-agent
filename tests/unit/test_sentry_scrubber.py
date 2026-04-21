"""Unit tests for the Sentry PII scrubber in app.main."""

from app.main import _scrub_sensitive_data

HINT = {}


def _event_with_headers(headers: dict) -> dict:
    return {"request": {"headers": headers, "method": "POST", "url": "http://example.com"}}


def _event_with_cookies(cookies: dict) -> dict:
    return {"request": {"cookies": cookies, "method": "GET", "url": "http://example.com"}}


# ---------------------------------------------------------------------------
# Header stripping
# ---------------------------------------------------------------------------


def test_authorization_header_stripped():
    event = _event_with_headers({"Authorization": "Bearer secret-token"})
    result = _scrub_sensitive_data(event, HINT)
    assert "Authorization" not in result["request"]["headers"]


def test_cookie_header_stripped():
    event = _event_with_headers({"Cookie": "session=abc123"})
    result = _scrub_sensitive_data(event, HINT)
    assert "Cookie" not in result["request"]["headers"]


def test_x_cron_secret_header_stripped():
    event = _event_with_headers({"X-Cron-Secret": "super-secret"})
    result = _scrub_sensitive_data(event, HINT)
    assert "X-Cron-Secret" not in result["request"]["headers"]


def test_uppercase_authorization_stripped():
    event = _event_with_headers({"AUTHORIZATION": "Bearer token"})
    result = _scrub_sensitive_data(event, HINT)
    assert "AUTHORIZATION" not in result["request"]["headers"]


def test_lowercase_cookie_stripped():
    event = _event_with_headers({"cookie": "session=xyz"})
    result = _scrub_sensitive_data(event, HINT)
    assert "cookie" not in result["request"]["headers"]


# ---------------------------------------------------------------------------
# Benign headers preserved
# ---------------------------------------------------------------------------


def test_benign_headers_preserved():
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html",
    }
    event = _event_with_headers(headers)
    result = _scrub_sensitive_data(event, HINT)
    assert result["request"]["headers"] == headers


# ---------------------------------------------------------------------------
# No request key
# ---------------------------------------------------------------------------


def test_no_request_key_returned_unchanged():
    event = {"exception": {"values": [{"type": "ValueError"}]}}
    result = _scrub_sensitive_data(event, HINT)
    assert result == event


def test_empty_request_value_returned_unchanged():
    event = {"request": None}
    result = _scrub_sensitive_data(event, HINT)
    assert result == event


# ---------------------------------------------------------------------------
# Cookies dict stripping
# ---------------------------------------------------------------------------


def test_cookies_dict_authorization_stripped():
    event = _event_with_cookies({"authorization": "Bearer token"})
    result = _scrub_sensitive_data(event, HINT)
    assert "authorization" not in result["request"]["cookies"]


def test_cookies_dict_cookie_key_stripped():
    event = _event_with_cookies({"cookie": "session=abc"})
    result = _scrub_sensitive_data(event, HINT)
    assert "cookie" not in result["request"]["cookies"]


def test_cookies_dict_x_cron_secret_stripped():
    event = _event_with_cookies({"x-cron-secret": "my-secret"})
    result = _scrub_sensitive_data(event, HINT)
    assert "x-cron-secret" not in result["request"]["cookies"]


def test_cookies_dict_benign_keys_preserved():
    cookies = {"session_id": "abc123", "theme": "dark"}
    event = _event_with_cookies(cookies)
    result = _scrub_sensitive_data(event, HINT)
    assert result["request"]["cookies"] == cookies

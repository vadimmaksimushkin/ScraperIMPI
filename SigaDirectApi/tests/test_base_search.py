"""Adversarial tests for base_search.

These probe the antiforgery handshake and the token-bearing request wiring
hard: TokenPair.cookies, fetch_token_pair's cookie-jar extraction, and
request_with_token's header/body/parse path. The hardening sections assert the
*intended* (correct) behaviour, now enforced: tuple-truthiness no longer lets an
empty antiforgery value through, a whitespace-only token is rejected, and an
empty-dict payload is sent as a body instead of being dropped.

No network: aiohttp sessions, responses and cookie jars are faked. Coroutines
are driven with asyncio.run() because pytest-asyncio is not installed.
"""

import asyncio

import orjson
import pytest

import base_search
from base_search import (
    BASE,
    CLIENT_TOKEN_COOKIE_HEADER,
    CLIENT_TOKEN_XSRF_HEADER,
    HEADERS_DEFAULT,
    ORIGIN,
    REFERER,
    TOKEN_URL,
    XSRF_COOKIE_NAME,
    TokenPair,
    fetch_token_pair,
    parse_token_headers,
    request_with_token,
)
from constants import RequestMethods

AF_KEY = ".AspNetCore.Antiforgery.AbCdEf"
AF_VAL = "antiforgery-cookie-value"
XSRF_VAL = "xsrf-token-value-123"
URL = f"{BASE}/api/Whatever"


# ===========================================================================
# Fakes / helpers
# ===========================================================================
class FakeCookie:
    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value


class _GetResp:
    async def read(self) -> bytes:
        return b""


class _GetCM:
    """Async context manager returned by session.get(...)."""

    def __init__(self, resp: _GetResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _GetResp:
        return self._resp

    async def __aexit__(self, *exc) -> bool:
        return False


class FakeJarSession:
    """Fakes the bits fetch_token_pair touches: .get() and .cookie_jar."""

    def __init__(self, cookies) -> None:
        self.cookie_jar = list(cookies)
        self.get_calls: list[tuple] = []

    def get(self, url: str, headers=None) -> _GetCM:
        self.get_calls.append((url, headers))
        return _GetCM(_GetResp())


class FakeReqResp:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text

    async def text(self) -> str:
        return self._text


class FakeReqSession:
    """Captures the kwargs passed to .request and returns a canned response."""

    def __init__(self, resp: FakeReqResp) -> None:
        self._resp = resp
        self.captured: dict | None = None

    async def request(self, **kwargs) -> FakeReqResp:
        self.captured = kwargs
        return self._resp


def jar(*pairs) -> list[FakeCookie]:
    return [FakeCookie(k, v) for k, v in pairs]


def run(coro):
    return asyncio.run(coro)


def _patch_token(monkeypatch, request_token: str = "reqtok123") -> None:
    async def fake_fetch(session):
        return TokenPair(AF_KEY, AF_VAL, request_token)

    monkeypatch.setattr(base_search, "fetch_token_pair", fake_fetch)


# ===========================================================================
# Module constants: shape / derivation (should PASS)
# ===========================================================================
def test_token_url_derives_from_base() -> None:
    assert TOKEN_URL == f"{BASE}/antiforgery/token"


def test_referer_is_origin_root() -> None:
    assert REFERER == f"{ORIGIN}/"


def test_origin_has_no_port_but_base_does() -> None:
    # Quirk worth pinning: requests go to BASE (:5007) but the Origin/Referer
    # CORS headers use the port-less host.
    assert ":5007" in BASE
    assert ":5007" not in ORIGIN


def test_headers_default_shape() -> None:
    assert set(HEADERS_DEFAULT) == {"Accept", "Origin", "Referer", "User-Agent"}


# ===========================================================================
# TokenPair.cookies: baseline (should PASS)
# ===========================================================================
def test_tokenpair_cookies_has_both() -> None:
    tp = TokenPair(AF_KEY, AF_VAL, XSRF_VAL)
    assert tp.cookies == {AF_KEY: AF_VAL, XSRF_COOKIE_NAME: XSRF_VAL}


def test_tokenpair_cookies_fresh_dict_each_call() -> None:
    tp = TokenPair(AF_KEY, AF_VAL, XSRF_VAL)
    assert tp.cookies is not tp.cookies


# ===========================================================================
# fetch_token_pair: baseline (should PASS)
# ===========================================================================
def test_fetch_returns_matched_pair() -> None:
    session = FakeJarSession(jar((AF_KEY, AF_VAL), (XSRF_COOKIE_NAME, XSRF_VAL)))
    tp = run(fetch_token_pair(session))
    assert (tp.cookie_name, tp.cookie_value, tp.request_token) == (AF_KEY, AF_VAL, XSRF_VAL)


def test_fetch_issues_get_to_token_url() -> None:
    session = FakeJarSession(jar((AF_KEY, AF_VAL), (XSRF_COOKIE_NAME, XSRF_VAL)))
    run(fetch_token_pair(session))
    assert session.get_calls == [(TOKEN_URL, HEADERS_DEFAULT)]


def test_fetch_antiforgery_prefix_bare_matches() -> None:
    session = FakeJarSession(jar((".AspNetCore.Antiforgery", AF_VAL), (XSRF_COOKIE_NAME, XSRF_VAL)))
    tp = run(fetch_token_pair(session))
    assert tp.cookie_name == ".AspNetCore.Antiforgery"


def test_fetch_ignores_unrelated_cookies() -> None:
    session = FakeJarSession(
        jar(("session", "x"), (AF_KEY, AF_VAL), ("other", "y"), (XSRF_COOKIE_NAME, XSRF_VAL))
    )
    tp = run(fetch_token_pair(session))
    assert tp.request_token == XSRF_VAL


def test_fetch_last_antiforgery_wins() -> None:
    # Two antiforgery cookies: the loop overwrites, keeping the last.
    session = FakeJarSession(
        jar(
            (".AspNetCore.Antiforgery.A", "first"),
            (".AspNetCore.Antiforgery.B", "second"),
            (XSRF_COOKIE_NAME, XSRF_VAL),
        )
    )
    tp = run(fetch_token_pair(session))
    assert tp.cookie_value == "second"


# ===========================================================================
# fetch_token_pair: failure handshakes (should PASS — they raise)
# ===========================================================================
def test_fetch_missing_xsrf_raises() -> None:
    session = FakeJarSession(jar((AF_KEY, AF_VAL)))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


def test_fetch_missing_antiforgery_raises() -> None:
    session = FakeJarSession(jar((XSRF_COOKIE_NAME, XSRF_VAL)))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


def test_fetch_empty_jar_raises() -> None:
    session = FakeJarSession([])
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


def test_fetch_empty_xsrf_value_raises() -> None:
    # request_token == "" is falsy, so this is correctly rejected.
    session = FakeJarSession(jar((AF_KEY, AF_VAL), (XSRF_COOKIE_NAME, "")))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


def test_fetch_near_miss_xsrf_name_not_matched() -> None:
    # Exact-match only: XSRF-TOKEN-BACKUP must not be taken as the token.
    session = FakeJarSession(jar((AF_KEY, AF_VAL), ("XSRF-TOKEN-BACKUP", XSRF_VAL)))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


# ===========================================================================
# fetch_token_pair: INTENDED behaviour — currently FAILS.
# ===========================================================================
def test_fetch_empty_antiforgery_value_should_raise() -> None:
    # An empty antiforgery cookie value is invalid, but the guard checks
    # `not antiforgery` on a 2-tuple (always truthy), so it slips through.
    session = FakeJarSession(jar((AF_KEY, ""), (XSRF_COOKIE_NAME, XSRF_VAL)))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


def test_fetch_whitespace_token_should_raise() -> None:
    # A whitespace-only token is not a real token; `not "   "` is False, so it
    # is accepted.
    session = FakeJarSession(jar((AF_KEY, AF_VAL), (XSRF_COOKIE_NAME, "   ")))
    with pytest.raises(RuntimeError):
        run(fetch_token_pair(session))


# ===========================================================================
# request_with_token: wiring + parse path (should PASS)
# ===========================================================================
def test_request_parses_json_body(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, '{"ok": true, "n": 3}'))
    status, body = run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}))
    assert status == 200
    assert body == {"ok": True, "n": 3}


def test_request_wires_headers_and_payload(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, "{}"))
    run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}))
    cap = session.captured
    assert cap["method"] == "POST"
    assert cap["url"] == URL
    assert cap["headers"]["x-xsrf-token"] == "reqtok123"
    assert cap["headers"]["Content-Type"] == "application/json"
    for key in HEADERS_DEFAULT:
        assert key in cap["headers"]
    assert cap["cookies"] == TokenPair(AF_KEY, AF_VAL, "reqtok123").cookies
    assert cap["data"] == orjson.dumps({"a": 1})


def test_request_honours_method(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, "{}"))
    run(request_with_token(session, RequestMethods.GET, URL, {"a": 1}))
    assert session.captured["method"] == "GET"


def test_request_non_json_returns_text(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(503, "Service Unavailable"))
    status, body = run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}))
    assert status == 503
    assert body == "Service Unavailable"


def test_request_empty_text_returns_empty_str(monkeypatch) -> None:
    # orjson.loads("") raises -> the JSONDecodeError fallback returns the text.
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(204, ""))
    status, body = run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}))
    assert status == 204
    assert body == ""


def test_request_none_payload_sends_no_body(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, "{}"))
    run(request_with_token(session, RequestMethods.POST, URL, None))
    assert session.captured["data"] is None


def test_request_bare_json_scalar_returned_as_python(monkeypatch) -> None:
    # Documents that the body isn't always dict/str: a bare JSON number parses.
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, "123"))
    _, body = run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}))
    assert body == 123


# ===========================================================================
# request_with_token: INTENDED behaviour — currently FAILS.
# ===========================================================================
def test_request_empty_dict_payload_sends_body(monkeypatch) -> None:
    # An empty dict {} is a valid JSON body that several SIGA endpoints REQUIRE
    # (the home export endpoints post `{}`), but `orjson.dumps(payload) if
    # payload else None` treats {} as falsy and sends no body at all.
    _patch_token(monkeypatch)
    session = FakeReqSession(FakeReqResp(200, "{}"))
    run(request_with_token(session, RequestMethods.POST, URL, {}))
    assert session.captured["data"] == orjson.dumps({})


# ===========================================================================
# Client-supplied token: parse_token_headers (should PASS)
# ===========================================================================
def test_parse_token_headers_none_when_both_absent() -> None:
    assert parse_token_headers({}) is None


def test_parse_token_headers_happy_path() -> None:
    tp = parse_token_headers({
        CLIENT_TOKEN_COOKIE_HEADER: f"{AF_KEY}={AF_VAL}",
        CLIENT_TOKEN_XSRF_HEADER: XSRF_VAL,
    })
    assert tp == TokenPair(AF_KEY, AF_VAL, XSRF_VAL)


def test_parse_token_headers_strips_whitespace() -> None:
    tp = parse_token_headers({
        CLIENT_TOKEN_COOKIE_HEADER: f"  {AF_KEY} = {AF_VAL}  ",
        CLIENT_TOKEN_XSRF_HEADER: f"  {XSRF_VAL}  ",
    })
    assert tp == TokenPair(AF_KEY, AF_VAL, XSRF_VAL)


def test_parse_token_headers_value_may_contain_equals() -> None:
    # antiforgery values are base64 and can end with '=' padding; partition on the
    # FIRST '=' so the value keeps its own.
    tp = parse_token_headers({
        CLIENT_TOKEN_COOKIE_HEADER: f"{AF_KEY}=CfDJ8abc==",
        CLIENT_TOKEN_XSRF_HEADER: XSRF_VAL,
    })
    assert tp == TokenPair(AF_KEY, "CfDJ8abc==", XSRF_VAL)


def test_parse_token_headers_only_cookie_raises() -> None:
    with pytest.raises(ValueError):
        parse_token_headers({CLIENT_TOKEN_COOKIE_HEADER: f"{AF_KEY}={AF_VAL}"})


def test_parse_token_headers_only_xsrf_raises() -> None:
    with pytest.raises(ValueError):
        parse_token_headers({CLIENT_TOKEN_XSRF_HEADER: XSRF_VAL})


def test_parse_token_headers_cookie_without_equals_raises() -> None:
    with pytest.raises(ValueError):
        parse_token_headers({
            CLIENT_TOKEN_COOKIE_HEADER: AF_KEY,  # no '=value'
            CLIENT_TOKEN_XSRF_HEADER: XSRF_VAL,
        })


def test_parse_token_headers_wrong_cookie_prefix_raises() -> None:
    with pytest.raises(ValueError):
        parse_token_headers({
            CLIENT_TOKEN_COOKIE_HEADER: f"SESSION={AF_VAL}",
            CLIENT_TOKEN_XSRF_HEADER: XSRF_VAL,
        })


# ===========================================================================
# Client-supplied token: request_with_token replays it, no handshake
# ===========================================================================
def test_request_with_supplied_token_skips_handshake(monkeypatch) -> None:
    # fetch_token_pair must NOT be called when a token is supplied.
    async def boom(session):
        raise AssertionError("fetch_token_pair should not be called")

    monkeypatch.setattr(base_search, "fetch_token_pair", boom)
    supplied = TokenPair(AF_KEY, AF_VAL, "supplied-req-tok")
    session = FakeReqSession(FakeReqResp(200, "{}"))
    run(request_with_token(session, RequestMethods.POST, URL, {"a": 1}, token=supplied))

    cap = session.captured
    assert cap["headers"]["x-xsrf-token"] == "supplied-req-tok"
    assert cap["cookies"] == supplied.cookies

import asyncio
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import aiohttp
import orjson
from constants import RequestMethods

log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

BASE = "https://siga.impi.gob.mx:5007"
TOKEN_URL = f"{BASE}/antiforgery/token"
ORIGIN = "https://siga.impi.gob.mx"
REFERER = f"{ORIGIN}/"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
ANTIFORGERY_COOKIE_PREFIX = ".AspNetCore.Antiforgery"
XSRF_COOKIE_NAME = "XSRF-TOKEN"

# Headers a client may use to supply its OWN antiforgery token, so the local API
# replays it upstream instead of fetching a fresh one per request. Funnelling every
# /antiforgery/token GET through the local API's single IP trips the anti-bot
# throttle; letting many client IPs mint their own tokens spreads that load.
# The cookie header carries the full ".AspNetCore.Antiforgery.*=<value>" pair; the
# xsrf header carries the matching request token. Both are required together.
CLIENT_TOKEN_COOKIE_HEADER = "X-Siga-Antiforgery-Cookie"
CLIENT_TOKEN_XSRF_HEADER = "X-Siga-Xsrf-Token"
HEADERS_DEFAULT = {
    "Accept": "application/json, text/plain, */*",
    "Origin": ORIGIN,
    "Referer": REFERER,
    "User-Agent": UA,
}
RESULTS_DIR = Path(__file__).parent / "Results"

CAP = 15000                 # server hard cap; a page of CAP means it was truncated
DEFAULT_YEARS_BACK = 20     # how far the pinned lower bound reaches
DEFAULT_DELAY_SECONDS = 2.0  # politeness delay between pages (per-IP rate limiting)


class AntiforgeryError(RuntimeError):
    """The /antiforgery/token handshake returned no usable cookie/token — usually
    the anti-bot throttling that endpoint (it answers 403 with no Set-Cookie).
    Subclasses RuntimeError so existing `except RuntimeError` paths still catch it."""


@dataclass
class TokenPair:
    """A matched antiforgery cookie + request token from one handshake"""

    cookie_name: str
    cookie_value: str
    request_token: str  # value of XSRF-TOKEN cookie, sent as x-xsrf-token header

    @property
    def cookies(self) -> dict[str, str]:
        return {
            self.cookie_name: self.cookie_value,
            XSRF_COOKIE_NAME: self.request_token,
        }


async def fetch_token_pair(session: aiohttp.ClientSession) -> TokenPair:
    """
    GET /antiforgery/token and extract the matched antiforgery cookie +
    request token
    """
    async with session.get(TOKEN_URL, headers=HEADERS_DEFAULT) as resp:
        await resp.read()

    antiforgery: tuple[str, str] | None = None
    request_token: str | None = None
    for cookie in session.cookie_jar:
        if cookie.key.startswith(ANTIFORGERY_COOKIE_PREFIX):
            antiforgery = (cookie.key, cookie.value)
        elif cookie.key == XSRF_COOKIE_NAME:
            request_token = cookie.value

    # both values must be present AND non-blank (a 2-tuple is always truthy, and a
    # whitespace-only token is not a real token)
    if (
        not antiforgery
        or not antiforgery[1].strip()
        or not request_token
        or not request_token.strip()
    ):
        raise AntiforgeryError(
            f"Antiforgery handshake failed: cookie={antiforgery!r}"
            f" token={request_token!r}"
        )
    return TokenPair(antiforgery[0], antiforgery[1], request_token)


def parse_token_headers(headers: Mapping[str, str]) -> TokenPair | None:
    """Build a TokenPair from the client-supplied token headers, or None when the
    client sent neither (the caller then fetches its own token). Raises ValueError
    if only one header is present or the cookie header is malformed — a half-given
    token is a client mistake, not a silent fall-through to a server-issued one."""
    cookie = headers.get(CLIENT_TOKEN_COOKIE_HEADER)
    xsrf = headers.get(CLIENT_TOKEN_XSRF_HEADER)

    if not cookie and not xsrf:
        return None
    if not cookie or not cookie.strip() or not xsrf or not xsrf.strip():
        raise ValueError(
            f"both {CLIENT_TOKEN_COOKIE_HEADER} and {CLIENT_TOKEN_XSRF_HEADER}"
            " are required together"
        )

    name, sep, value = cookie.partition("=")
    name, value = name.strip(), value.strip()
    if not sep or not name or not value:
        raise ValueError(
            f"{CLIENT_TOKEN_COOKIE_HEADER} must be '<cookie-name>=<cookie-value>'"
        )
    if not name.startswith(ANTIFORGERY_COOKIE_PREFIX):
        raise ValueError(
            f"{CLIENT_TOKEN_COOKIE_HEADER} name must start with"
            f" {ANTIFORGERY_COOKIE_PREFIX!r}"
        )
    return TokenPair(name, value, xsrf.strip())


async def request_with_token(
    session: aiohttp.ClientSession,
    method: RequestMethods,
    url: str,
    payload: dict[str, Any] | None,
    token: TokenPair | None = None,
) -> tuple[int, Any]:
    """Call an endpoint that validates the antiforgery token. When `token` is given
    (e.g. minted and supplied by the client) it is replayed as-is; otherwise a fresh
    token is fetched. Replaying a supplied token avoids a /antiforgery/token GET per
    request — the endpoint the anti-bot throttles."""
    if token is None:
        token = await fetch_token_pair(session)

    headers = {
        **HEADERS_DEFAULT,
        "Content-Type": "application/json",
        "x-xsrf-token": token.request_token,
    }
    res = await session.request(
        method=method.name,
        url=url,
        headers=headers,
        cookies=token.cookies,
        data=orjson.dumps(payload) if payload is not None else None,
    )

    text = await res.text()
    try:
        return res.status, orjson.loads(text)
    except orjson.JSONDecodeError:
        return res.status, text


async def request_no_token(
    session: aiohttp.ClientSession,
    method: RequestMethods,
    url: str,
    payload: dict[str, Any] | None,
) -> tuple[int, Any]:
    """Like request_with_token, but skips the antiforgery handshake. Some endpoints
    (proven: GetImagenArray) don't validate the token, so calling them token-less
    avoids a /antiforgery/token GET per request — the endpoint the anti-bot
    throttles. Returns (status, parsed_json_or_text), same as request_with_token."""
    headers = {**HEADERS_DEFAULT, "Content-Type": "application/json"}
    res = await session.request(
        method=method.name,
        url=url,
        headers=headers,
        data=orjson.dumps(payload) if payload is not None else None,
    )
    text = await res.text()
    try:
        return res.status, orjson.loads(text)
    except orjson.JSONDecodeError:
        return res.status, text


if __name__ == "__main__":
    async def test() -> None:
        payload_by_gaceta: dict[str, Any] = {
            "idArea":"2",
            "idGaceta":"35",
            "fechaDesde":None,
            "fechaHasta":None,
            "reCaptchaToken":"",
        }
        url_by_gaceta = f"{BASE}/api/DescargaEjemplares/GetEjemplares"


        async with aiohttp.ClientSession() as session:
            status, res = await request_with_token(
                session,
                RequestMethods.POST,
                url_by_gaceta,
                payload_by_gaceta,
            )
            log.info(status)
            log.info(res)
    asyncio.run(test())

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from enum import Enum, auto

import aiohttp
import orjson

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


class RequestMethods(Enum):
    GET = auto()
    HEAD = auto()
    POST = auto()
    PUT = auto()
    DELETE = auto()
    OPTIONS = auto()
    PATCH = auto()


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

    if not antiforgery or not request_token:
        raise RuntimeError(
            f"Antiforgery handshake failed: cookie={antiforgery!r}"
            f" token={request_token!r}"
        )
    return TokenPair(antiforgery[0], antiforgery[1], request_token)


async def request_with_token(
    session: aiohttp.ClientSession,
    method: RequestMethods,
    url: str,
    payload: dict[str, Any] | None,
) -> tuple[int, Any]:
    token = await fetch_token_pair(session)
    log.info(token)

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
        data=orjson.dumps(payload) if payload else None,
    )

    text = await res.text()
    log.info(text)
    try:
        return res.status, orjson.loads(text)
    except orjson.JSONDecodeError:
        return res.status, text


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

if __name__ == "__main__":
    asyncio.run(test())

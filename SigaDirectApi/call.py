import aiohttp
import asyncio
import logging
import sys
from typing import Any
from dataclasses import dataclass
import orjson
from datetime import datetime, timezone
from pathlib import Path


log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


BASE = "https://siga.impi.gob.mx:5007"
TOKEN_URL = f"{BASE}/antiforgery/token"
SEARCH_URL = f"{BASE}/api/BusquedaEstructurada/GetSearchEstructurada"
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
DEFAULT_PAYLOAD: dict[str, Any] = {
    "idArea": "2",
    "FechaDesde": "01-01-2025", # dd-mm-yyyy
    "FechaHasta": "10-06-2026",
    "idGaceta": [35],
    "idSeccion": [100188],
    "datos": [
        {
        "operador": None,
        "columna": "Clase",
        "valor": "42",
        "fecha": None
        },
    ],
    "reCaptchaToken": "", # required field, accepts empty string
}
RESULTS_DIR = Path(__file__).parent / "Results"


@dataclass
class TokenPair:
    cookie_name: str
    cookie_value: str
    request_token: str  # value of XSRF-TOKEN cookie -> sent as x-xsrf-token header

    @property
    def cookies(self) -> dict[str, str]:
        return {
            self.cookie_name: self.cookie_value,
            XSRF_COOKIE_NAME: self.request_token
        }


async def fetch_token_pair(session: aiohttp.ClientSession) -> TokenPair:
    async with session.get(
        TOKEN_URL,
        headers=HEADERS_DEFAULT,
    ) as resp:
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


async def search(
    session: aiohttp.ClientSession,
    token: TokenPair,
    payload: dict[str, Any] | None = DEFAULT_PAYLOAD,
) -> tuple[int, Any]:
    """POST a structured search using a fresh token pair. Returns (status, parsed_json_or_text)."""
    headers = HEADERS_DEFAULT
    headers["Content-Type"] = "application/json"
    headers["x-xsrf-token"] = token.request_token

    async with session.post(
        SEARCH_URL,
        data=orjson.dumps(payload),
        headers=headers,
    ) as resp:
        text = await resp.text()
        try:
            return resp.status, orjson.loads(text)
        except orjson.JSONDecodeError:
            return resp.status, text


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        headers = HEADERS_DEFAULT
        headers["Content-Type"] = "application/json"
        headers["x-xsrf-token"] = token.request_token
        status, body = await search(session, token, DEFAULT_PAYLOAD)
        log.info(f"{status} | len: {len(body)} | records: {body.keys()}")
        log.info(f"successed: {body.get("successed", "field is empty")}")
        log.info(f"message: {body.get("message", "field is empty")}")
        log.info(f"errors: {body.get("errors", "field is empty")}")
        log.info(f"len(data): {len(body.get("data", []))}")
        empty_data: list[str] = ["No info was found"]
        data: list[Any] = body.get("data", empty_data)
        if not len(data):
            data = empty_data
        log.info(f"{orjson.dumps(
            data[0],
            option=orjson.OPT_INDENT_2,
        ).decode()}")

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT-%H-%M-%SZ")
        out = RESULTS_DIR / f"{ts}.json"
        out.write_bytes(orjson.dumps(
            body,
            option=orjson.OPT_INDENT_2,
        ))
        log.info(f"Saved -> {out}")


if __name__ == "__main__":
    asyncio.run(main())

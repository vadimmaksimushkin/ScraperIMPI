import aiohttp
import asyncio
import base64
import logging
import re
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
# image GET
FICHA_ID_TEST = "bktjVzNHYnBxbjhhajJmZTBnVEVTQT09"
IMAGE_URL = f"https://siga.impi.gob.mx:5007/api/BusquedaFicha/GetImage?idFicha={FICHA_ID_TEST}"

# copies search
SEARCH_URL_COPIES = "https://siga.impi.gob.mx:5007/api/DescargaEjemplares/GetEjemplares"
PAYLOAD_COPIES_DEFAULT: dict[str, Any] = {
    "idArea": "2",
    "idGaceta": "35",
    "fechaDesde": None,
    "fechaHasta": None,
    "reCaptchaToken": "",
}
# records search
SEARCH_URL_RECORDS = "https://siga.impi.gob.mx:5007/api/BusquedaFicha/GetFichas"
PAYLOAD_RECORDS_DEFAULT: dict[str, Any] = {
    "busqueda": "3618676",
    "idArea": "",
    "idGaceta": [],
    "fechaDesde": "",
    "fechaHasta": "",
    "reCaptchaToken": "",
}
# advanced search
SEARCH_URL_ADVANCED = f"{BASE}/api/BusquedaEstructurada/GetSearchEstructurada"
PAYLOAD_ADVANCED_DEFAULT: dict[str, Any] = {
#     "idArea": "2",
#     "FechaDesde": "01-01-2025", # dd-mm-yyyy
#     "FechaHasta": "10-06-2026",
#     "idGaceta": [35],
#     "idSeccion": [100188],
#     "datos": [
#         {
#             "operador": None,
#             "columna": "Clase",
#             "valor": "42",
#             "fecha": None
#         },
#     ],
#     "reCaptchaToken": "", # required field, accepts empty string
# }
# PAYLOAD_ADVANCED_v2: dict[str, Any] = {
    "idArea": "2",
    "FechaDesde": None,
    "FechaHasta": None,
    "idGaceta": [],
    "idSeccion": [],
    "datos": [
        {
            "operador": None,
            "columna": "Clase",
            "valor": "42",
            "fecha": None
        },
    ],
    "reCaptchaToken": "",
}
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

# --- Images (see debug/ReferencesAndFiles/SIGA_API_REFERENCE.md §13) ---------
# Two ways to fetch a ficha's images:
#   (a) GetImagenArray POST {id: <plain int fichaId>} -> data.imagenBase64[] (ALL images, no crypto)
#   (b) GetImage GET ?idFicha=<convertid(fichaId)>     -> single principal image blob (AES id)
IMAGE_GET_URL = f"{BASE}/api/BusquedaFicha/GetImage"
IMAGE_ARRAY_URL = f"{BASE}/api/DescargaEjemplares/GetImagenArray"
IMAGES_DIR = RESULTS_DIR / "images"

# AES id encoding. key/iv recovered from the bundle env object; proven by the
# round-trip convertid(15231022) == FICHA_ID_TEST.
AES_KEY = b"T3B9vku6S=-Z6wGK"
AES_IV = b"U-q6s#pFa{Kb4vy_"

# fichaIds with images, from the user's GetSearchEstructurada responses
# (gaceta 35 / Solicitudes de Marcas, 19/06/2026). label is just for filenames.
IMAGE_FICHAS_TEST: list[tuple[int, str]] = [
    (15253160, "SHOPPERX"),                  # countImagen 1
    (15254387, "MAMMAPRINT_BLUEPRINT"),      # countImagen 1
    (15231022, "known_FICHA_ID_TEST"),       # convertid -> the baked-in FICHA_ID_TEST
]


def btoa(value: Any) -> str:
    """JS btoa: base64 of the string. Used for copies getPDF/getXML `{id}`."""
    return base64.b64encode(str(value).encode()).decode()


def convertid(value: Any) -> str:
    """GetImage / viewer-pdf id: btoa(base64(AES-128-CBC/PKCS7(str(value)))) -> double base64.

    Lazily imports pycryptodome so the plain-id GetImagenArray path keeps working
    even if it is not installed.
    """
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad

    ct = AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(
        pad(str(value).encode(), AES.block_size)
    )
    return base64.b64encode(base64.b64encode(ct)).decode()


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_")
    return (s[:40] or "ficha")


def _img_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "bin"


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
    payload: dict[str, Any] | None = PAYLOAD_ADVANCED_DEFAULT,
) -> tuple[int, Any]:
    """POST a structured search using a fresh token pair. Returns (status, parsed_json_or_text)."""
    headers = HEADERS_DEFAULT
    headers["Content-Type"] = "application/json"
    headers["x-xsrf-token"] = token.request_token

    async with session.post(
        SEARCH_URL_ADVANCED,
        data=orjson.dumps(payload),
        headers=headers,
    ) as resp:
        text = await resp.text()
        try:
            return resp.status, orjson.loads(text)
        except orjson.JSONDecodeError:
            return resp.status, text


async def fetch_image_array(
    session: aiohttp.ClientSession,
    token: TokenPair,
    ficha_id: int,
) -> list[bytes]:
    """POST GetImagenArray {id: fichaId} (plain int). Returns every image as bytes."""
    headers = dict(HEADERS_DEFAULT)
    headers["Content-Type"] = "application/json"
    headers["x-xsrf-token"] = token.request_token

    async with session.post(
        IMAGE_ARRAY_URL,
        data=orjson.dumps({"id": ficha_id}),
        headers=headers,
    ) as resp:
        status = resp.status
        text = await resp.text()

    if status != 200:
        log.error(f"GetImagenArray {ficha_id} -> HTTP {status}: {text[:200]}")
        return []
    try:
        body = orjson.loads(text)
    except orjson.JSONDecodeError:
        log.error(f"GetImagenArray {ficha_id} -> non-JSON: {text[:200]}")
        return []

    arr = (body.get("data") or {}).get("imagenBase64") or []
    out: list[bytes] = []
    for b64 in arr:
        if isinstance(b64, str) and b64.startswith("data:") and "," in b64:
            b64 = b64.split(",", 1)[1]  # strip a data-URL prefix if present
        try:
            out.append(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001
            log.error(f"GetImagenArray {ficha_id} -> bad base64 entry: {exc}")
    return out


async def fetch_image_get(
    session: aiohttp.ClientSession,
    token: TokenPair,
    ficha_id: int,
) -> bytes | None:
    """GET GetImage?idFicha=convertid(fichaId). Returns the principal image bytes."""
    headers = dict(HEADERS_DEFAULT)
    headers["x-xsrf-token"] = token.request_token

    async with session.get(
        IMAGE_GET_URL,
        params={"idFicha": convertid(ficha_id)},
        headers=headers,
    ) as resp:
        status = resp.status
        ctype = resp.headers.get("Content-Type", "")
        data = await resp.read()

    if status != 200 or "image" not in ctype.lower():
        log.error(
            f"GetImage {ficha_id} -> HTTP {status} ctype={ctype!r} "
            f"bytes={len(data)} head={data[:120]!r}"
        )
        return None
    return data


async def test_images() -> None:
    """Fetch images for the known fichaIds via both paths and save them to disk."""
    assert convertid(15231022) == FICHA_ID_TEST, "convertid mismatch vs FICHA_ID_TEST"
    log.info("convertid self-check OK (convertid(15231022) == FICHA_ID_TEST)")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        for ficha_id, label in IMAGE_FICHAS_TEST:
            slug = _slug(label)

            # (a) GetImagenArray: plain id, all images
            imgs = await fetch_image_array(session, token, ficha_id)
            log.info(f"[{ficha_id} {label}] GetImagenArray -> {len(imgs)} image(s)")
            for i, raw in enumerate(imgs):
                out = IMAGES_DIR / f"{ficha_id}_{slug}_array{i}.{_img_ext(raw)}"
                out.write_bytes(raw)
                log.info(f"    saved {out.name} ({len(raw)} bytes)")

            # (b) GetImage: AES id, single principal image
            raw = await fetch_image_get(session, token, ficha_id)
            if raw:
                out = IMAGES_DIR / f"{ficha_id}_{slug}_get.{_img_ext(raw)}"
                out.write_bytes(raw)
                log.info(f"[{ficha_id} {label}] GetImage -> saved {out.name} ({len(raw)} bytes)")

    log.info(f"Images saved under -> {IMAGES_DIR}")


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        headers = HEADERS_DEFAULT
        headers["Content-Type"] = "application/json"
        headers["x-xsrf-token"] = token.request_token
        status, body = await search(session, token, PAYLOAD_ADVANCED_DEFAULT)
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
    if len(sys.argv) > 1 and sys.argv[1] == "images":
        asyncio.run(test_images())
    else:
        asyncio.run(main())

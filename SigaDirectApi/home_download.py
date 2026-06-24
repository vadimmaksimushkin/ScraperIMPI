import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote
import aiohttp
import orjson
import logging
import sys
from typing import Any, Literal
from base_search import (
    BASE,
    HEADERS_DEFAULT,
    fetch_token_pair,
)
from constants import RequestMethods

DOWNLOAD_PATH = Path(__file__).parent.parent / "SIGA IMPI GACETAS"
URL_XLSX = f"{BASE}/api/BusquedaFicha/ExportExcelToday"
URL_PDF = f"{BASE}/api/BusquedaFicha/ExportPDFToday"

log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


async def download_request(
    session: aiohttp.ClientSession,
    url: str,
    method: RequestMethods = RequestMethods.POST,
    payload: dict[str, Any] | list[Any] | None = None,
) -> aiohttp.ClientResponse:
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
        data=orjson.dumps(payload or {}),
    )

    return res


# RFC 5987 extended form: filename*=charset'lang'pct-encoded (takes precedence)
_FILENAME_STAR_RE = re.compile(r"filename\*\s*=\s*([^;]+)", re.IGNORECASE)
# Plain form: filename="quoted, may contain ;" or filename=bare-token
_FILENAME_RE = re.compile(r'filename\s*=\s*("[^"]*"|[^;]+)', re.IGNORECASE)


def _safe_basename(name: str, fallback: str) -> str:
    """Reduce a server-controlled name to a harmless basename: no path separators
    or traversal, no control chars, within the filesystem's 255-byte limit."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]   # last path component only
    name = "".join(ch for ch in name if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    name = name.strip().strip(".")                      # kills "", ".", ".."
    if not name:
        return fallback
    if len(name.encode()) > 255:
        stem, dot, ext = name.rpartition(".")
        if dot and len(ext) < 250:
            name = stem.encode()[: 255 - len(ext) - 1].decode("utf-8", "ignore") + "." + ext
        else:
            name = name.encode()[:255].decode("utf-8", "ignore")
    return name


def get_filename_from_headers(
    res: aiohttp.ClientResponse,
    fallback: str = "filename_was_not_returned",
) -> str:
    cd = res.headers.get("Content-Disposition", "")
    star = _FILENAME_STAR_RE.search(cd)
    if star:
        raw = star.group(1).strip().strip('"')
        if raw.count("'") >= 2:                          # charset'lang'value -> value
            raw = raw.split("'", 2)[2]
        name = unquote(raw)
    else:
        plain = _FILENAME_RE.search(cd)
        if not plain:
            return fallback
        name = plain.group(1).strip().strip('"')
    return _safe_basename(name, fallback)


async def download_archive(
    session: aiohttp.ClientSession,
    type: Literal["xlsx", "pdf"],
    download_dir: Path | None = None,
) -> Path | None:
    url = ""
    if type not in ("xlsx", "pdf"):
        raise ValueError("type must be xlsx or pdf")

    if type == "xlsx":
        url = URL_XLSX
    if type == "pdf":
        url = URL_PDF

    res = await download_request(
        session=session,
        method=RequestMethods.POST,
        url=url,
        payload={},
    )
    # A 404 with the sentinel body just means "nothing published yet today"; its
    # body is tiny so reading it is cheap. Any other non-200 is a real error.
    if res.status != 200:
        body = await res.read()
        if res.status == 404 and body.strip() == b"No se encontraron archivos PDF para descargar.":
            log.info("Today there're no new archives")
            return None
        raise RuntimeError(f"[download_archive] status={res.status} body={body[:200]!r}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = get_filename_from_headers(res, f"archive_{ts}.{type}")

    download_directory = download_dir or DOWNLOAD_PATH
    download_directory.mkdir(parents=True, exist_ok=True)
    downloaded_file = download_directory / name
    # Stream to disk in 64 KiB chunks so a 200 MB archive never sits whole in RAM.
    with downloaded_file.open("wb") as fh:
        async for chunk in res.content.iter_chunked(1 << 16):
            fh.write(chunk)
    return downloaded_file


async def download_todays_archive(
    *types: Literal["xlsx", "pdf"],
    download_dir: Path | None = None,
) -> list[Path]:
    saved: list[Path] = []
    download_types: set[str] = set()
    for type in types:
        if type == "xlsx":
            download_types.add(type)
        if type == "pdf":
            download_types.add(type)

    async with aiohttp.ClientSession() as session:
        for type in download_types:
            filename = await download_archive(session=session, type=type, download_dir=download_dir) # type: ignore
            if filename:
                saved.append(filename)
    return saved


if __name__ == "__main__":
    async def test() -> None:
        def human_size(size: float) -> str:
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if abs(size) < 1024:
                    return f"{size:.2f} {unit}"
                size /= 1024
            return f"{size:.2f} PB"

        files = await download_todays_archive("xlsx", "pdf")
        for file in files:
            size_bytes = file.stat().st_size
            log.info(f"Saved {file} ({human_size(size_bytes)})")

    asyncio.run(test())

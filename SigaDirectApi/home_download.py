import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
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
    payload: dict[str, Any] = {},
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
        data=orjson.dumps(payload),
    )

    return res


def get_filename_from_headers(
    res: aiohttp.ClientResponse,
    fallback: str = "filename_was_not_returned",
) -> str:
    cd = res.headers.get("Content-Disposition", "")
    match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    return match.group(1) if match else fallback


async def download_archive(
    session: aiohttp.ClientSession,
    type: Literal["xlsx", "pdf"],
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
    body = await res.read()
    if res.status == 404 and body == b"No se encontraron archivos PDF para descargar.":
        log.info("Today there're no new archives")
        return None
    elif res.status != 200:
        raise RuntimeError(f"[download_archive] status={res.status} body={body[:200]!r}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = get_filename_from_headers(res, f"archive_{ts}.{type}")

    download_directory = DOWNLOAD_PATH
    download_directory.mkdir(exist_ok=True)
    downloaded_file = download_directory / name
    downloaded_file.write_bytes(body)
    return downloaded_file


async def download_todays_archive(*types: Literal["xlsx", "pdf"]) -> list[Path]:
    saved: list[Path] = []
    download_types: set[str] = set()
    for type in types:
        if type == "xlsx":
            download_types.add(type)
        if type == "pdf":
            download_types.add(type)

    async with aiohttp.ClientSession() as session:
        for type in download_types:
            filename = await download_archive(session=session, type=type) # type: ignore
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

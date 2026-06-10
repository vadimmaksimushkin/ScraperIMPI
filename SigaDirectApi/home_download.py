"""
Home (Inicio) archive downloads — the two export icons over "today's gacetas".

  xls icon -> POST /api/BusquedaFicha/ExportExcelToday  (xlsx blob)
  pdf icon -> POST /api/BusquedaFicha/ExportPDFToday    (pdf  blob)

Both take Content-Type application/json with an EMPTY `{}` body and return the file
as the raw response body (responseType: blob) — no id, no params. Antiforgery token
still required (SIGA_API_REFERENCE.md §5).

(The per-row PublicGacetaPDF links need an AES-encrypted id and are out of scope.)
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import orjson

from base_search import (
    BASE,
    HEADERS_DEFAULT,
    TokenPair,
    fetch_token_pair,
)

URL_XLSX = f"{BASE}/api/BusquedaFicha/ExportExcelToday"
URL_PDF = f"{BASE}/api/BusquedaFicha/ExportPDFToday"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

EXPORTS: dict[str, tuple[str, str]] = {
    # kind -> (url, default extension)
    "xlsx": (URL_XLSX, "xlsx"),
    "xls": (URL_XLSX, "xlsx"),
    "pdf": (URL_PDF, "pdf"),
}


def _filename_from_headers(resp: aiohttp.ClientResponse, fallback: str) -> str:
    """Prefer the server's Content-Disposition filename; else the fallback."""
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    return m.group(1) if m else fallback


async def download_export(
    session: aiohttp.ClientSession,
    token: TokenPair,
    kind: str,
    *,
    out_dir: Path = DOWNLOAD_DIR,
) -> Path:
    """Download one home archive (`xlsx`/`xls`/`pdf`) and save it; returns the path."""
    if kind not in EXPORTS:
        raise ValueError(f"unknown export kind {kind!r}; expected one of {list(EXPORTS)}")
    url, ext = EXPORTS[kind]
    headers = {
        **HEADERS_DEFAULT,
        "Content-Type": "application/json",
        "x-xsrf-token": token.request_token,
    }
    async with session.post(
        url, data=orjson.dumps({}), headers=headers, cookies=token.cookies
    ) as resp:
        body = await resp.read()
        if resp.status != 200:
            raise RuntimeError(
                f"[home {kind}] status={resp.status} body={body[:200]!r}"
            )
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        name = _filename_from_headers(resp, f"inicio_{ts}.{ext}")

    out_dir.mkdir(exist_ok=True)
    out = out_dir / name
    out.write_bytes(body)
    return out


async def download_archives(*kinds: str) -> list[Path]:
    """Token handshake + download each requested kind (default xlsx + pdf)."""
    wanted = kinds or ("xlsx", "pdf")
    saved: list[Path] = []
    async with aiohttp.ClientSession() as session:
        for kind in wanted:
            token = await fetch_token_pair(session)
            path = await download_export(session, token, kind)
            print(f"[home] {kind} -> {path} ({path.stat().st_size} bytes)")
            saved.append(path)
    return saved


async def main() -> None:
    await download_archives("xlsx", "pdf")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import base64
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from base_search import BASE, RequestMethods
from home_download import download_request, get_filename_from_headers

log = logging.getLogger("siga.export")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

# Copies (ejemplares) — single ejemplar PDF/XML use the plain-base64 id
# (DescargaEjemplares, §13); a multi-id batch uses ExportEjemplares (array of
# {id, isHTML, isXML}). Fichas use FichasGaceta with a plain {id: [fichaId,...]}.
URL_COPIES_PDF_SINGLE = f"{BASE}/api/DescargaEjemplares/getPDF"
URL_COPIES_XML_SINGLE = f"{BASE}/api/DescargaEjemplares/getXML"
URL_COPIES_PDF_BATCH = f"{BASE}/api/BusquedaFicha/ExportEjemplaresPDF"
URL_COPIES_XLSX_BATCH = f"{BASE}/api/BusquedaFicha/ExportEjemplaresEXCEL"
URL_FICHAS_PDF = f"{BASE}/api/BusquedaFicha/FichasGacetaPDF"
URL_FICHAS_XLSX = f"{BASE}/api/BusquedaFicha/FichasGacetaXLS"

INT64_MAX = 2**63 - 1  # widest fixed-width integer any backend could represent

COPIES_FORMATS = ("pdf", "xml", "xlsx")  # xml: single only; xlsx: batch only
FICHAS_FORMATS = ("pdf", "xlsx")

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "xml": "application/xml",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


def btoa(value: int) -> str:
    """JS btoa of the id — the plain-base64 id the copies getPDF/getXML expect."""
    return base64.b64encode(str(value).encode()).decode()


def validate(format: str, ids: list[int], allowed: tuple[str, ...]) -> None:
    if not isinstance(format, str) or format.lower() not in allowed:  # type: ignore
        raise ValueError(f"format must be one of {allowed}")
    if not isinstance(ids, list) or not ids:  # type: ignore
        raise ValueError("id must be a non-empty list of ints")
    for i in ids:
        if isinstance(i, bool) or not isinstance(i, int):  # type: ignore
            raise ValueError("id must contain only ints")
        if i < 1:
            raise ValueError("ids must be positive ints")
        if i > INT64_MAX:
            raise ValueError("an id is too large for the backend to represent")


def resolve_copies(format: str, ids: list[int]) -> tuple[str, RequestMethods, Any]:
    """(url, method, payload) for an ejemplares export. Single id + pdf/xml hits
    the per-ejemplar endpoint; everything else is an ExportEjemplares batch."""
    validate(format, ids, COPIES_FORMATS)
    fmt = format.lower()
    n = len(ids)
    if fmt == "xml":
        if n != 1:
            raise ValueError("xml export is single-ejemplar only (pass exactly one id)")
        return URL_COPIES_XML_SINGLE, RequestMethods.POST, {"id": btoa(ids[0])}
    if fmt == "pdf" and n == 1:
        return URL_COPIES_PDF_SINGLE, RequestMethods.POST, {"id": btoa(ids[0])}
    # batch pdf / any xlsx -> ExportEjemplares (array of {id, isHTML, isXML}).
    # Tested 2026-06-22 across all 4 combos: the server IGNORES isHTML/isXML — the
    # output zip always bundles the ejemplar PDF + an XML + an index file, no HTML.
    # So defaulting both True is safe (the values don't matter). PDF vs EXCEL only
    # changes the small "Ejemplares por pagina" index (.pdf vs .xlsx); both zips are
    # ~equally large (they carry the full PDF+XML).
    payload = [{"id": i, "isHTML": True, "isXML": True} for i in ids]
    url = URL_COPIES_PDF_BATCH if fmt == "pdf" else URL_COPIES_XLSX_BATCH
    return url, RequestMethods.POST, payload


def resolve_fichas(format: str, ids: list[int]) -> tuple[str, RequestMethods, Any]:
    """(url, method, payload) for a fichas export (FichasGaceta, {id: [...]})."""
    validate(format, ids, FICHAS_FORMATS)
    fmt = format.lower()
    url = URL_FICHAS_PDF if fmt == "pdf" else URL_FICHAS_XLSX
    return url, RequestMethods.POST, {"id": ids}


def media_type_for_filename(name: str) -> str:
    n = (name or "").lower()
    for ext, mime in (
        (".pdf", MEDIA_TYPES["pdf"]),
        (".xlsx", MEDIA_TYPES["xlsx"]),
        (".xml", MEDIA_TYPES["xml"]),
        (".zip", MEDIA_TYPES["zip"]),
    ):
        if n.endswith(ext):
            return mime
    return "application/octet-stream"


async def request_export(
    session: aiohttp.ClientSession,
    kind: str,
    format: str,
    ids: list[int],
) -> aiohttp.ClientResponse:
    """Fire the upstream export and return the raw response so the caller can
    stream it (directly to the client for fichas, or to disk for copies)."""
    if kind == "copies":
        url, method, payload = resolve_copies(format, ids)
    elif kind == "fichas":
        url, method, payload = resolve_fichas(format, ids)
    else:
        raise ValueError(f"unknown export kind: {kind!r}")
    return await download_request(session, url=url, method=method, payload=payload)


async def export_to_disk(
    session: aiohttp.ClientSession,
    kind: str,
    format: str,
    ids: list[int],
    download_dir: Path,
) -> Path:
    """Run an export and stream it to disk in 64 KiB chunks (a copies export can
    be hundreds of MB to GB+, so it never sits whole in RAM). Returns the path."""
    res = await request_export(session, kind=kind, format=format, ids=ids)
    if res.status != 200:
        body = await res.read()
        raise RuntimeError(
            f"export {kind}/{format} status={res.status} body={body[:200]!r}"
        )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = get_filename_from_headers(res, f"{kind}_{format.lower()}_{ts}")
    download_dir.mkdir(parents=True, exist_ok=True)
    out = download_dir / name
    with out.open("wb") as fh:
        async for chunk in res.content.iter_chunked(1 << 16):
            fh.write(chunk)
    return out


if __name__ == "__main__":

    async def test() -> None:
        # fichas export is quick; copies (esp. batch) can be large.
        async with aiohttp.ClientSession() as session:
            out = await export_to_disk(
                session, kind="copies", format="pdf", ids=[22638], download_dir=Path("Results/exports")
            )
            log.info("saved %s (%d bytes)", out, out.stat().st_size)

    asyncio.run(test())

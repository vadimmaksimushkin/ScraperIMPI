"""
Copies (Ejemplares) search — POST /api/DescargaEjemplares/GetEjemplares
                          OR  POST /api/DescargaEjemplares/GetEjemplaresArrayByFecha

The copies form hits ONE of two endpoints depending on what's filled in
(SIGA_API_REFERENCE.md §3.1–3.2):

  * a Gaceta is chosen          -> GetEjemplares            (idGaceta single string)
  * only Área + a date range    -> GetEjemplaresArrayByFecha (idGaceta forced null)

Dates are `YYYY-MM-DD` (lowercase `fechaDesde`/`fechaHasta`); empty -> null.
`build_payload` picks the endpoint + shapes the payload accordingly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from base_search import (
    BASE,
    SearchSpec,
    TokenPair,
    fetch_token_pair,
    post_json,
)
import SigaDirectApi.OtherScripts.choices as choices

# Endpoints
URL_BY_GACETA = f"{BASE}/api/DescargaEjemplares/GetEjemplares"
URL_BY_FECHA = f"{BASE}/api/DescargaEjemplares/GetEjemplaresArrayByFecha"

# Static choices (mirrors the form dropdowns)
AREAS = choices.AREAS                       # incl "0" Extraordinarios (copies-only)
GACETAS = choices.GACETAS
gacetas_for_area = choices.gacetas_for_area

SPEC = SearchSpec(
    name="copies",
    url=URL_BY_GACETA,
    payload_desde_key="fechaDesde",         # lowercase f
    payload_hasta_key="fechaHasta",
    payload_date_fmt="%Y-%m-%d",            # YYYY-MM-DD (NOT %d-%m-%Y)
    list_key="data",
    record_id_key="idEjemplar",             # GUESS — verify from a real response
    record_date_key="fechaPuestaCirculacion",
    record_date_fmt="%d/%m/%Y",
)

# GetEjemplares — when a Gaceta is selected (dates optional, null when empty).
PAYLOAD_BY_GACETA: dict[str, Any] = {
    "idArea": "2",
    "idGaceta": "35",          # single string
    "fechaDesde": "2026-06-01",  # YYYY-MM-DD or null
    "fechaHasta": "2026-06-10",
    "reCaptchaToken": "",
}

# GetEjemplaresArrayByFecha — Área + date range only (no gaceta).
PAYLOAD_BY_FECHA: dict[str, Any] = {
    "idArea": "0",             # e.g. Extraordinarios
    "idGaceta": None,          # always null on this endpoint
    "fechaDesde": "2026-05-01",
    "fechaHasta": "2026-06-10",
    "reCaptchaToken": "",
}

PAYLOAD_DEFAULT = PAYLOAD_BY_GACETA


def build_payload(
    id_area: str,
    *,
    id_gaceta: str | None = None,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
    recaptcha: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    Pick the endpoint the SPA would use and shape the matching payload.
    Returns (url, payload). Dates are `YYYY-MM-DD`.
    """
    if id_gaceta is not None:
        return URL_BY_GACETA, {
            "idArea": str(id_area),
            "idGaceta": str(id_gaceta),
            "fechaDesde": fecha_desde or None,
            "fechaHasta": fecha_hasta or None,
            "reCaptchaToken": recaptcha,
        }
    return URL_BY_FECHA, {
        "idArea": str(id_area),
        "idGaceta": None,
        "fechaDesde": fecha_desde,
        "fechaHasta": fecha_hasta,
        "reCaptchaToken": recaptcha,
    }


async def search(
    session: aiohttp.ClientSession,
    token: TokenPair,
    payload: dict[str, Any] | None = None,
    *,
    url: str = URL_BY_GACETA,
) -> tuple[int, Any]:
    """Single Ejemplares call (one page). Pass `url` to hit the by-fecha endpoint."""
    return await post_json(session, token, url, payload or PAYLOAD_DEFAULT)


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        url, payload = build_payload("2", id_gaceta="35",
                                     fecha_desde="2026-06-01", fecha_hasta="2026-06-10")
        status, body = await search(session, token, payload, url=url)
        n = len(body.get("data", [])) if isinstance(body, dict) else "-"
        print(f"[copies] {url.rsplit('/', 1)[-1]} -> status={status} records={n}")


if __name__ == "__main__":
    asyncio.run(main())

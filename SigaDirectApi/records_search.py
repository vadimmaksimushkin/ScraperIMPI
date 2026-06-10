"""
Records (Fichas) search — POST /api/BusquedaFicha/GetFichas.

Free-text / expediente search over fichas (`busqueda` = the term, required, min 2
chars), with optional área / gaceta / date window.

Inconsistencies vs the other two searches (SIGA_API_REFERENCE.md §1):
  * dates are `YYYY-MM-DD`, lowercase keys, and the empty sentinel is `""`
    (empty string) — NOT null like copies/advanced.
  * `idGaceta` is an ARRAY (`[]` = any); `idArea` is a string (`""` = any).
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

# Static choices (mirrors the "Opciones avanzadas" dropdowns)
AREAS = choices.AREAS
GACETAS = choices.GACETAS
gacetas_for_area = choices.gacetas_for_area

SPEC = SearchSpec(
    name="records",
    url=f"{BASE}/api/BusquedaFicha/GetFichas",
    payload_desde_key="fechaDesde",
    payload_hasta_key="fechaHasta",
    payload_date_fmt="%Y-%m-%d",            # YYYY-MM-DD (NOT %d-%m-%Y)
    list_key="data",
    record_id_key="fichaId",                # GUESS — verify from a real response
    record_date_key="fechaPuestaCirculacion",
    record_date_fmt="%d/%m/%Y",
)

PAYLOAD_DEFAULT: dict[str, Any] = {
    "busqueda": "3618676",     # required, min 2 chars
    "idArea": "",              # "" = any
    "idGaceta": [],            # array; [] = any
    "fechaDesde": "",          # YYYY-MM-DD or "" (empty string, NOT null)
    "fechaHasta": "",
    "reCaptchaToken": "",
}


def build_payload(
    busqueda: str,
    *,
    id_area: str = "",
    id_gaceta: list[int] | None = None,
    fecha_desde: str = "",
    fecha_hasta: str = "",
    recaptcha: str = "",
) -> dict[str, Any]:
    """Shape a GetFichas payload. Dates `YYYY-MM-DD`; empty -> `""`."""
    return {
        "busqueda": busqueda,
        "idArea": str(id_area),
        "idGaceta": id_gaceta or [],
        "fechaDesde": fecha_desde or "",
        "fechaHasta": fecha_hasta or "",
        "reCaptchaToken": recaptcha,
    }


async def search(
    session: aiohttp.ClientSession,
    token: TokenPair,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Single GetFichas call (one page)."""
    return await post_json(session, token, SPEC.url, payload or PAYLOAD_DEFAULT)


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        status, body = await search(session, token, build_payload("3618676"))
        n = len(body.get("data", [])) if isinstance(body, dict) else "-"
        print(f"[records] GetFichas -> status={status} records={n}")


if __name__ == "__main__":
    asyncio.run(main())

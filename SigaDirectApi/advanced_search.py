"""
Advanced (structured) search — POST /api/BusquedaEstructurada/GetSearchEstructurada.

Filter by área / gaceta / sección and a list of column predicates (`datos`) over a
date window. The 15000-record cap endpoint (no pagination/sort param).

Inconsistencies vs the other two searches (SIGA_API_REFERENCE.md §1):
  * date keys are CAPITALISED `FechaDesde`/`FechaHasta` and the format is
    `DD-MM-YYYY` (empty -> null).
  * `idGaceta` and `idSeccion` are ARRAYS OF INT (`[35]`, `[100188]`).
  * `datos[]` = {operador, columna, valor, fecha}; a date column puts its value in
    `fecha` (DD/MM/YYYY) instead of `valor`.

Response envelope: {successed, message, errors, data:[{fichaId, areaId, ejemplar,
gaceta, seccion, fechaPuestaCirculacion("DD/MM/YYYY"), imagen, countImagen,
vinculos, datos:[{descripcion, datoTxt, orden}]}]}.
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

# Static choices (mirrors the form dropdowns). Secciones/Columnas are
# per-gaceta/per-seccion — only the Marcas baseline is bundled; fetch others live.
AREAS = choices.AREAS
GACETAS = choices.GACETAS
SECCIONES_BY_GACETA = choices.SECCIONES_BY_GACETA
COLUMNAS_MARCAS = choices.COLUMNAS_MARCAS
OPERADORES = choices.OPERADORES

SPEC = SearchSpec(
    name="advanced",
    url=f"{BASE}/api/BusquedaEstructurada/GetSearchEstructurada",
    payload_desde_key="FechaDesde",         # capital F
    payload_hasta_key="FechaHasta",
    payload_date_fmt="%d-%m-%Y",            # DD-MM-YYYY
    list_key="data",
    record_id_key="fichaId",
    record_date_key="fechaPuestaCirculacion",
    record_date_fmt="%d/%m/%Y",
)

# Validated baseline (2026-06-09): Marcas · gaceta 35 · sección 100188 · Clase 42.
PAYLOAD_DEFAULT: dict[str, Any] = {
    "idArea": "2",
    "FechaDesde": None,        # DD-MM-YYYY or null
    "FechaHasta": None,
    "idGaceta": [],            # array of int
    "idSeccion": [],           # array of int
    "datos": [
        {"operador": None, "columna": "Clase", "valor": "42", "fecha": None},
    ],
    "reCaptchaToken": "",
}


def termino(
    columna: str,
    valor: str,
    *,
    operador: str | None = None,
    es_fecha: bool = False,
) -> dict[str, Any]:
    """
    One `datos[]` predicate. A date column (`es_fecha`/`Fecha...`) puts `valor` in
    `fecha` (DD/MM/YYYY); everything else uses `valor`. `operador` only matters with
    >=2 terms (one of OPERADORES).
    """
    is_date = es_fecha or columna.startswith("Fecha")
    return {
        "operador": operador,
        "columna": columna,
        "valor": None if is_date else valor,
        "fecha": valor if is_date else None,
    }


def build_payload(
    id_area: str,
    datos: list[dict[str, Any]],
    *,
    fecha_desde: str | None = None,   # DD-MM-YYYY
    fecha_hasta: str | None = None,
    id_gaceta: list[int] | None = None,
    id_seccion: list[int] | None = None,
    recaptcha: str = "",
) -> dict[str, Any]:
    """Shape a GetSearchEstructurada payload (capital Fecha keys, array ids)."""
    return {
        "idArea": str(id_area),
        "FechaDesde": fecha_desde,
        "FechaHasta": fecha_hasta,
        "idGaceta": id_gaceta or [],
        "idSeccion": id_seccion or [],
        "datos": datos,
        "reCaptchaToken": recaptcha,
    }


async def search(
    session: aiohttp.ClientSession,
    token: TokenPair,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Single structured-search call (one page, capped at 15000)."""
    return await post_json(session, token, SPEC.url, payload or PAYLOAD_DEFAULT)


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        token = await fetch_token_pair(session)
        payload = build_payload(
            "2",
            [termino("Clase", "42")],
            fecha_desde="01-05-2026", fecha_hasta="09-06-2026",
            id_gaceta=[35], id_seccion=[100188],
        )
        status, body = await search(session, token, payload)
        n = len(body.get("data", [])) if isinstance(body, dict) else "-"
        print(f"[advanced] GetSearchEstructurada -> status={status} records={n}")


if __name__ == "__main__":
    asyncio.run(main())

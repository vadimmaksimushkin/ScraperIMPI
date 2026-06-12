import asyncio
import aiohttp
from typing import Any
import logging
import sys
from datetime import date
from base_search import (
    BASE,
    RequestMethods,
    request_with_token,
)
from constants import (
    Area,
    Gaceta,
    Seccion,
    Operador,
    Columna,
    Dato,
    SECCION_COLUMNAS,
)

log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

URL=f"{BASE}/api/BusquedaEstructurada/GetSearchEstructurada"


# FIXME: set SECCION_COLUMNAS: dict[Gaceta, set[Columna]] in constants.py
GACETA_COLUMNAS: dict[Gaceta, set[Columna]] = {}
for _sec, _cols in SECCION_COLUMNAS.items():
    GACETA_COLUMNAS.setdefault(_sec.gaceta, set()).update(_cols)


def input_validation(
    area: Area,
    gacetas: list[Gaceta] | None,
    secciones: list[Seccion] | None,
    datos: list[Dato],
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[bool, str]:
    # date range: both present or both absent, and ordered
    if bool(fecha_desde) != bool(fecha_hasta):  # XOR
        return False, "Both dates must be present or absent"
    if fecha_desde is not None and fecha_hasta is not None and fecha_hasta < fecha_desde:
        return False, "fecha_desde must be <= fecha_hasta"

    # area is required
    if area is None: # pyright: ignore[reportUnnecessaryComparison]
        return False, "area is required"

    # seccion can't be selected without gaceta
    if not gacetas and secciones:
        return False, "secciones require at least one gaceta"

    # Area -> Gaceta -> Seccion must coincide
    for g in gacetas or []:
        if g.area is not area:
            message = f"gaceta {g.name} is in {g.area.name}, not {area.name}"
            return False, message
    for s in secciones or []:
        if s.gaceta.area is not area:
            message = (
                f"seccion {s.name} is in {s.gaceta.area.name},"
                f" not {area.name}"
            )
            return False, message

    # datos: 1 or 2 terms; first operador is empty, second non-empty
    if not datos:
        return False, "at least one dato is required"
    if len(datos) > 2:
        return False, "at most two datos are allowed"
    if datos[0].operador is not Operador.EMPTY:
        return False, "first dato must have an empty operador"
    if len(datos) == 2 and datos[1].operador is Operador.EMPTY:
        return False, "second dato must have a non-empty operador (AND/OR/NOT)"

    # valor/fecha consistency is enforced in Dato.__init__
    # here we only check that each column is searchable across
    # every selected gaceta and seccion
    for d in datos:
        for g in gacetas or []:
            if d.columna not in GACETA_COLUMNAS.get(g, set()):
                message = (
                    f"{d.columna.name} is not a valid column"
                    f" for gaceta {g.name}"
                )
                return False, message
        for s in secciones or []:
            if d.columna not in SECCION_COLUMNAS[s]:
                message = (
                    f"{d.columna.name} is not a valid column"
                    f" for seccion {s.name}"
                )
                return False, message

    if not isinstance(recaptcha, str): # type: ignore
        return False, "recaptcha must be the type str"

    return True, "OK"


def build_payload(
    area: Area,
    gacetas: list[Gaceta],
    secciones: list[Seccion],
    datos: list[Dato],
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> dict[str, Any]:
    ok, message = input_validation(
        area=area,
        gacetas=gacetas,
        secciones=secciones,
        datos=datos,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        recaptcha=recaptcha,
    )
    if not ok:
        raise ValueError(message)

    payload: dict[str, Any] = {
        "idArea": str(area.value) if area else "",
        "idGaceta": [gaceta.id_gaceta for gaceta in gacetas] if gacetas else [],
        "idSeccion": [seccion.id_seccion for seccion in secciones] if secciones else [],
        "datos": [dato.to_payload() for dato in datos],
        "FechaDesde": fecha_desde.strftime("%d-%m-%Y") if fecha_desde else "", # can be null
        "FechaHasta": fecha_hasta.strftime("%d-%m-%Y") if fecha_hasta else "", # can be null
        "reCaptchaToken": recaptcha,
    }
    return payload

async def search(
    session: aiohttp.ClientSession,
    area: Area,
    gacetas: list[Gaceta],
    secciones: list[Seccion],
    datos: list[Dato],
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[int, Any]:
    payload = build_payload(
        area=area,
        gacetas=gacetas,
        secciones=secciones,
        datos=datos,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        recaptcha=recaptcha,
    )
    status, res = await request_with_token(
        session=session,
        method=RequestMethods.POST,
        url=URL,
        payload=payload,
    )
    return status, res


if __name__ == "__main__":
    async def test() -> None:
        area = Area.MARCAS
        gacetas = [
            Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO,
            Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO,
        ]
        secciones = [
            Seccion.SOLICITUDES_DE_AVISOS_COMERCIALES_100190,
            Seccion.SOLICITUDES_DE_MARCAS_100188,
            Seccion.SOLICITUDES_DE_NOMBRES_COMERCIALES_100192,
        ]
        datos = [
            Dato(
                operador=Operador.EMPTY,
                columna=Columna.CLASE,
                valor="42",
            ),
            Dato(
                operador=Operador.OR,
                columna=Columna.DENOMINACION,
                valor="ZESTO"
            ),
        ]
        fecha_desde = date(2026, 1, 1)
        fecha_hasta = date(2026, 6, 11)

        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                area=area,
                gacetas=gacetas,
                secciones=secciones,
                datos=datos,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )
            log.info(status)
            log.info(len(res.get("data", [])))

    asyncio.run(test())

# test_payload_v1: dict[str, Any] = {
#     "idArea": "2",
#     "FechaDesde": "01-01-2026",
#     "FechaHasta": "11-06-2026",
#     "idGaceta": [37],
#     "idSeccion": [],
#     "datos": [
#         {
#             "operador": "",
#             "columna": "Clase",
#             "valor": "42",
#             "fecha":""
#         },
#         {
#             "operador": "OR",
#             "columna": "Clase (s)",
#             "valor": "40",
#             "fecha": ""
#         }
#     ],
#     "reCaptchaToken": "",
# }
# test_payload_v2: dict[str, Any] = {
#     "idArea":"2",
#     "FechaDesde":"01-01-2026",
#     "FechaHasta":"11-06-2026",
#     "idGaceta":[37,1],
#     "idSeccion":[406],
#     "datos": [
#         {
#             "operador":"",
#             "columna":"Clase",
#             "valor":"42",
#             "fecha":"",
#         },
#     ],
#     "reCaptchaToken":"",
# }
# test_payload_v3: dict[str, Any] = {
#     "idArea":"2",
#     "FechaDesde":"01-01-2026",
#     "FechaHasta":"11-06-2026",
#     "idGaceta":[37],
#     "idSeccion":[406],
#     "datos": [
#         {
#             "operador":None,
#             "columna":"Autorización",
#             "valor":"termino",
#             "fecha":None,
#         },
#         {
#             "operador":"OR",
#             "columna":"Usuario Autorizado",
#             "valor":"termino",
#             "fecha":""
#         },
#     ],
#     "reCaptchaToken":"",
# }
# test_payload_v4: dict[str, Any] = {
#     "idArea":"1",
#     "FechaDesde": None,
#     "FechaHasta": None,
#     "idGaceta": [],
#     "idSeccion": [],
#     "datos": [
#         {
#             "operador": "",
#             "columna": "Fecha de petición",
#             "valor": "\"",
#             "fecha": "2026-06-10T06:00:00.000Z"
#         },
#         {
#             "operador": "OR",
#             "columna": "Fecha de Boletín",
#             "valor": "\"",
#             "fecha": "2026-06-09T06:00:00.000Z"
#         }
#     ],
#     "reCaptchaToken": "",
# }

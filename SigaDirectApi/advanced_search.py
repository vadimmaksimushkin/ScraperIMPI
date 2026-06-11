import asyncio
import aiohttp
from typing import Any
import logging
import sys
from enum import Enum
from dataclasses import dataclass
from datetime import date
from base_search import (
    BASE,
    RequestMethods,
    request_with_token,
)
from copies_search import Area, Gaceta

log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

URL=f"{BASE}/api/BusquedaEstructurada/GetSearchEstructurada"

test_payload_v1: dict[str, Any] = {
    "idArea": "2",
    "FechaDesde": "01-01-2026",
    "FechaHasta": "11-06-2026",
    "idGaceta": [37],
    "idSeccion": [],
    "datos": [
        {
            "operador": "",
            "columna": "Clase",
            "valor": "42",
            "fecha":""
        },
        {
            "operador": "OR",
            "columna": "Clase (s)",
            "valor": "40",
            "fecha": ""
        }
    ],
    "reCaptchaToken": "",
}
test_payload_v2: dict[str, Any] = {
    "idArea":"2",
    "FechaDesde":"01-01-2026",
    "FechaHasta":"11-06-2026",
    "idGaceta":[37,1],
    "idSeccion":[406],
    "datos": [
        {
            "operador":"",
            "columna":"Clase",
            "valor":"42",
            "fecha":"",
        },
    ],
    "reCaptchaToken":"",
}

test_payload_v3: dict[str, Any] = {
    "idArea":"2",
    "FechaDesde":"01-01-2026",
    "FechaHasta":"11-06-2026",
    "idGaceta":[37],
    "idSeccion":[406],
    "datos": [
        {
            "operador":None,
            "columna":"Autorización",
            "valor":"termino",
            "fecha":None,
        },
        {
            "operador":"OR",
            "columna":"Usuario Autorizado",
            "valor":"termino",
            "fecha":""
        },
    ],
    "reCaptchaToken":"",
}


class Seccion(Enum):
    def __init__(self, id_seccion: int, gaceta: Gaceta):
        self.id_seccion = id_seccion
        self.gaceta = gaceta

    SOLICITUDES_DE_MARCAS                   = (100188, Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO)
    SOLICITUDES_DE_AVISOS_COMERCIALES       = (100190, Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO)
    SOLICITUDES_DE_NOMBRES_COMERCIALES      = (100192, Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO)


class Operador(Enum):
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    EMPTY = ""


class Columna(Enum):
    CLASE = "Clase"
    CLASE_S = "Clase (s)"


@dataclass
class Dato:
    operador: Operador
    columna: Columna
    valor: str
    fecha: str = ""


def build_payload(
    area: Area,
    gacetas: list[Gaceta],
    secciones: list[Seccion],
    datos: list[Dato],
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> dict[str, Any]:
    if bool(fecha_desde) != bool(fecha_hasta): #XOR(fecha_desde, fecha_hasta)
        raise ValueError("Both dates must be present or absent")
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_hasta < fecha_desde:
            raise ValueError("fechas_desde must be less or equeal to fecha_hasta")

    payload: dict[str, Any] = {
        "idArea": str(area.value) if area else "",
        "idGaceta": [gaceta.id_gaceta for gaceta in gacetas] if gacetas else [],
        "idSeccion": [seccion.id_seccion for seccion in secciones] if secciones else [],
        "datos": [dato.__dict__ for dato in datos], # FIXME: check that dataclass returns dict
        "FechaDesde": fecha_desde.strftime("%d-%m-%Y") if fecha_desde else "",
        "FechaHasta": fecha_hasta.strftime("%d-%m-%Y") if fecha_hasta else "",
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
            Seccion.SOLICITUDES_DE_AVISOS_COMERCIALES,
            Seccion.SOLICITUDES_DE_MARCAS,
            Seccion.SOLICITUDES_DE_NOMBRES_COMERCIALES,
        ]
        datos = [
            Dato(
                operador=Operador.EMPTY,
                columna=Columna.CLASE,
                valor="42",
            ),
            Dato(
                operador=Operador.OR,
                columna=Columna.CLASE_S,
                valor="40"
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

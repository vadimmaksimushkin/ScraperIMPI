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
from copies_search import Area, Gaceta

log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

URL=f"{BASE}/api/BusquedaFicha/GetFichas"


def build_payload(
    busqueda: int,
    area: Area | None = None,
    gacetas: list[Gaceta] | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> dict[str, Any]:
    if bool(area) != bool(gacetas): # XOR(area, gacetas)
        raise ValueError("Gacetas and Area must be either both present or absent")
    if gacetas and area:
        for gaceta in gacetas:
            if gaceta.area.value != area.value:
                raise ValueError("Gacetas must be in the same Area")

    if bool(fecha_desde) != bool(fecha_hasta): #XOR(fecha_desde, fecha_hasta)
        raise ValueError("Both dates must be present or absent")
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_hasta < fecha_desde:
            raise ValueError("fechas_desde must be less or equeal to fecha_hasta")

    # empty fecha == ""
    # date format %d-%m-%Y
    payload: dict[str, Any] = {
        "busqueda": str(busqueda), # "3618676"
        "idArea": str(area.value) if area else "",
        "idGaceta": [gaceta.id_gaceta for gaceta in gacetas] if gacetas else [],
        "fechaDesde": fecha_desde.strftime("%d-%m-%Y") if fecha_desde else "",
        "fechaHasta": fecha_hasta.strftime("%d-%m-%Y") if fecha_hasta else "",
        "reCaptchaToken": recaptcha,
    }
    return payload

async def search(
    session: aiohttp.ClientSession,
    busqueda: int,
    area: Area | None = None,
    gacetas: list[Gaceta] | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[int, Any]:
    payload = build_payload(
        busqueda=busqueda,
        area=area,
        gacetas=gacetas,
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
    async def test_empty() -> None:
        busqueda = 3618676
        area = None
        gacetas = None
        fecha_desde = None
        fecha_hasta = None
        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                busqueda=busqueda,
                area=area,
                gacetas=gacetas,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )
            log.info(status)
            log.info(res)

    async def test_full() -> None:
        busqueda = 3618676
        area = Area.MARCAS
        gacetas = [
            Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO,
            Gaceta.SOLICITUDES_DE_MARCAS_NOMBRES_COMERCIALES_Y_AVISOS_COMERCIALES_ABANDONADAS,
        ]
        fecha_desde = date(2026, 1, 1)
        fecha_hasta = date(2026, 6, 11)
        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                busqueda=busqueda,
                area=area,
                gacetas=gacetas,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )
            log.info(status)
            log.info(res)


    asyncio.run(test_empty())
    asyncio.run(asyncio.sleep(10))
    asyncio.run(test_full())

import asyncio
import aiohttp
from typing import Any
from datetime import date
import logging
import sys
from base_search import (
    BASE,
    RequestMethods,
    request_with_token,
)
from constants import Area, Gaceta
log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


URL_BY_GACETA = f"{BASE}/api/DescargaEjemplares/GetEjemplares"
URL_BY_FECHA = f"{BASE}/api/DescargaEjemplares/GetEjemplaresArrayByFecha"


def build_url_payload(
    area: Area,
    gaceta: Gaceta | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[str, dict[str, Any]]:
    if not gaceta and not fecha_desde and not fecha_hasta:
        message = (
            "Invalid arguments: area must be present, gaceta may be absent"
            " if fechas are present, fechas may be absent if gaceta is present"
        )
        raise ValueError(message)
    if bool(fecha_desde) != bool(fecha_hasta): #XOR(fecha_desde, fecha_hasta)
        raise ValueError("Both dates must be present or absent")
    if fecha_desde is not None and fecha_hasta is not None:
        if fecha_hasta < fecha_desde:
            raise ValueError("fechas_desde must be less or equeal to fecha_hasta")

    request_endpoint: str = ""
    payload: dict[str, Any] = {
        "fechaDesde": fecha_desde.strftime("%Y-%m-%d") if fecha_desde else None,
        "fechaHasta": fecha_hasta.strftime("%Y-%m-%d") if fecha_hasta else None,
        "reCaptchaToken": recaptcha,
    }

    if gaceta:
        request_endpoint = URL_BY_GACETA
        payload["idArea"] = str(gaceta.area.value)
        payload["idGaceta"] = str(gaceta.id_gaceta)
    else:
        request_endpoint = URL_BY_FECHA
        payload["idArea"] = str(area.value)
        payload["idGaceta"] = None
    return (request_endpoint, payload)


async def search(
    session: aiohttp.ClientSession,
    area: Area,
    gaceta: Gaceta | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[int, Any]:
    url, payload = build_url_payload(
        area=area,
        gaceta=gaceta,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        recaptcha=recaptcha,
    )
    status, res = await request_with_token(
        session=session,
        method=RequestMethods.POST,
        url=url,
        payload=payload,
    )
    return status, res


if __name__ == "__main__":
    async def test() -> None:
        area = Area.MARCAS
        gaceta = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO

        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                area=area,
                gaceta=gaceta,
            )
            log.info(status)
            log.info(len(res.get("data", [])))


    async def test_array_by_fecha() -> None:
        area = Area.MARCAS
        fecha_desde = date(2026, 5, 1)
        fecha_hasta = date(2026, 6, 10)

        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                area=area,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )
            log.info(status)
            log.info(len(res.get("data", [])))


    async def test_full() -> None:
        area = Area.MARCAS
        gaceta = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
        fecha_desde = date(2026, 5, 1)
        fecha_hasta = date(2026, 6, 10)

        async with aiohttp.ClientSession() as session:
            status, res = await search(
                session=session,
                area=area,
                gaceta=gaceta,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
            )
            log.info(status)
            log.info(len(res.get("data", [])))

    asyncio.run(test())
    asyncio.run(asyncio.sleep(10))
    asyncio.run(test_array_by_fecha())
    asyncio.run(asyncio.sleep(10))
    asyncio.run(test_full())

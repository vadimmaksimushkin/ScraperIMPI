import asyncio
import aiohttp
from typing import Any
from datetime import date, datetime, timedelta
import logging
import sys
from base_search import (
    BASE,
    DEFAULT_YEARS_BACK,
    RequestMethods,
    request_with_token,
)
from constants import Area, Gaceta, RECAPTCHA_TOKEN_RE, mexico_today
log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


URL_BY_GACETA = f"{BASE}/api/DescargaEjemplares/GetEjemplares"
URL_BY_FECHA = f"{BASE}/api/DescargaEjemplares/GetEjemplaresArrayByFecha"


def input_validation( # NOSONAR
    area: Area,
    gaceta: Gaceta | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[bool, str]:
    # parameter types
    if not isinstance(area, Area): # type: ignore
        return False, "area must be an Area"
    if gaceta is not None and not isinstance(gaceta, Gaceta): # type: ignore
        return False, "gaceta must be a Gaceta"
    if gaceta is not None and gaceta.area is not area:
        return False, "gaceta must be in the given area"
    if fecha_desde is not None and not isinstance(fecha_desde, date): # type: ignore
        return False, "fecha_desde must be a date"
    if fecha_hasta is not None and not isinstance(fecha_hasta, date): # type: ignore
        return False, "fecha_hasta must be a date"
    if not isinstance(recaptcha, str): # type: ignore
        return False, "recaptcha must be the type str"
    if recaptcha and not RECAPTCHA_TOKEN_RE.fullmatch(recaptcha):
        return False, "recaptcha has an invalid format"

    # a datetime is not a pure date; floor it (time-of-day is meaningless here)
    if isinstance(fecha_desde, datetime):
        fecha_desde = fecha_desde.date()
    if isinstance(fecha_hasta, datetime):
        fecha_hasta = fecha_hasta.date()

    # need a gaceta or a date range to scope the download
    if not gaceta and not fecha_desde and not fecha_hasta:
        message = (
            "either gaceta or a date range is required"
            " (gaceta may be absent if fechas are present, and vice versa)"
        )
        return False, message

    # date range: both present or both absent, ordered, within the design floor,
    # and not in the future
    if bool(fecha_desde) != bool(fecha_hasta): # XOR(fecha_desde, fecha_hasta)
        return False, "Both dates must be present or absent"
    if fecha_desde is not None and fecha_hasta is not None and fecha_hasta < fecha_desde:
        return False, "fecha_desde must be <= fecha_hasta"
    if fecha_desde is not None and fecha_desde < mexico_today() - timedelta(days=365 * DEFAULT_YEARS_BACK):
        return False, f"fecha_desde must be within the last {DEFAULT_YEARS_BACK} years"
    if fecha_hasta is not None and fecha_hasta > mexico_today():
        return False, "dates must be <= current date"

    return True, "OK"


def build_url_payload(
    area: Area,
    gaceta: Gaceta | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[str, dict[str, Any]]:
    ok, message = input_validation(
        area=area,
        gaceta=gaceta,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        recaptcha=recaptcha,
    )
    if not ok:
        raise ValueError(message)

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
            data_dict: dict[str, Any] =res.get("data", {})
            total_length = 0
            for k, v in data_dict.items():
                total_length += len(v)
                log.info(f"{k}: {len(v)}")
            log.info(f"data total lenght {total_length}")


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

    # asyncio.run(test())
    # asyncio.run(asyncio.sleep(10))
    asyncio.run(test_array_by_fecha())
    # asyncio.run(asyncio.sleep(10))
    # asyncio.run(test_full())

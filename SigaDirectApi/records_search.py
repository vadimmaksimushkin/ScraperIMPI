import asyncio
import aiohttp
from typing import Any
import logging
import sys
from datetime import date, datetime
from base_search import (
    BASE,
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

URL=f"{BASE}/api/BusquedaFicha/GetFichas"

INT64_MAX = 2**63 - 1  # widest fixed-width integer any backend could represent


def is_list_of(lst: object, T: type) -> bool:
    return isinstance(lst, list) and all(isinstance(item, T) for item in lst) # type: ignore


def input_validation( # NOSONAR
    busqueda: int,
    area: Area | None = None,
    gacetas: list[Gaceta] | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[bool, str]:
    # busqueda is required: an int (not bool) of at least 2 digits (>= 10)
    if isinstance(busqueda, bool) or not isinstance(busqueda, int): # type: ignore
        return False, "busqueda must be the type int"
    if busqueda < 10:
        return False, "busqueda must be at least 2 digits long"
    if busqueda > INT64_MAX:
        return False, "busqueda is too large for the backend to represent"

    # parameter types
    if area is not None and not isinstance(area, Area): # type: ignore
        return False, "area must be an Area"
    if gacetas is not None and not is_list_of(gacetas, Gaceta):
        return False, "gacetas must be a list of Gaceta"
    if gacetas is not None and len(gacetas) > len(Gaceta):
        return False, "too many gacetas (more than exist)"
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

    # area and gacetas are scoped together
    if bool(area) != bool(gacetas): # XOR(area, gacetas)
        return False, "Gacetas and Area must be either both present or absent"
    if gacetas and area:
        for gaceta in gacetas:
            if gaceta.area.value != area.value:
                return False, "Gacetas must be in the same Area"

    # date range: both present or both absent, ordered, and not in the future
    if bool(fecha_desde) != bool(fecha_hasta): # XOR(fecha_desde, fecha_hasta)
        return False, "Both dates must be present or absent"
    if fecha_desde is not None and fecha_hasta is not None and fecha_hasta < fecha_desde:
        return False, "fecha_desde must be <= fecha_hasta"
    if fecha_hasta is not None and fecha_hasta > mexico_today():
        return False, "dates must be <= current date"

    return True, "OK"


def build_payload(
    busqueda: int,
    area: Area | None = None,
    gacetas: list[Gaceta] | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> dict[str, Any]:
    ok, message = input_validation(
        busqueda=busqueda,
        area=area,
        gacetas=gacetas,
        fecha_desde=fecha_desde,
        fecha_hasta=fecha_hasta,
        recaptcha=recaptcha,
    )
    if not ok:
        raise ValueError(message)

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

import asyncio
import aiohttp
from typing import Any
from enum import Enum
from datetime import date
import logging
import sys

from base_search import (
    BASE,
    RequestMethods,
    request_with_token,
)
log = logging.getLogger("siga.search")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


URL_BY_GACETA = f"{BASE}/api/DescargaEjemplares/GetEjemplares"
URL_BY_FECHA = f"{BASE}/api/DescargaEjemplares/GetEjemplaresArrayByFecha"


class Area(Enum):
    PATENTES = 1
    MARCAS = 2
    PROTECCION_A_LA_PROPIEDAD_INTELECTUAL = 3
    AREA_7 = 7 # FIXME: set Area 7
    AREA_8 = 8 # FIXME: set Area 8


class Gaceta(Enum):
    def __init__(self, id_gaceta: int, area: Area):
            self.id_gaceta = id_gaceta
            self.area = area

    SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES          = (2, Area.PATENTES)
    PATENTES_REGISTROS_DE_MODELOS_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES                          = (3, Area.PATENTES)
    LICENCIAS_TRANSMISIONES_Y_CAMBIOS_EN_SOLICITUDES_PATENTES_Y_REGISTROS                        = (10, Area.PATENTES)
    PATENTES_VIGENTES_SUSCEPTIBLES_DE_SER_EMPLEADAS_EN_MEDICAMENTOS_ALOPATICOS_ART_162_LFPPI     = (17, Area.PATENTES)
    REQUISITOS_DE_EXAMEN_DE_FORMA_Y_FONDO_ABANDONOS_DE_SOLICITUDES_DE_PATENTES_Y_REGISTROS       = (24, Area.PATENTES)
    INVENCIONES_DE_DOMINIO_PUBLICO_Y_SOLICITUDES_DE_USO_LIBRE                                    = (29, Area.PATENTES)
    PATENTES_VIGENTES_QUE_INCUMPLEN_LO_DISPUESTO_EN_EL_ART_162_LFPPI                             = (39, Area.PATENTES)
    NOTIFICACIONES_DE_LA_DIRECCION_DIVISIONAL_DE_PATENTES                                        = (41, Area.PATENTES)
    RENOVACIONES_DE_DISENOS_INDUSTRIALES                                                         = (42, Area.PATENTES)
    MARCAS_REGISTRADAS_AVISOS_Y_NOMBRES_COMERCIALES                                              = (1, Area.MARCAS)
    SOLICITUDES_DE_MARCAS_NOMBRES_COMERCIALES_Y_AVISOS_COMERCIALES_ABANDONADAS                   = (18, Area.MARCAS)
    OFICIOS_REFERENTES_A_SIGNOS_DISTINTIVOS                                                      = (30, Area.MARCAS)
    SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO             = (35, Area.MARCAS)
    OPOSICION_A_SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES                               = (36, Area.MARCAS)
    CONSERVACION_DE_LOS_DERECHOS                                                                 = (37, Area.MARCAS)
    NOTIFICACION_DE_RESOLUCIONES_REQUERIMIENTOS_Y_DEMAS_ACTOS                                    = (40, Area.MARCAS)
    SIGNOS_DISTINTIVOS_CADUCOS                                                                   = (81, Area.MARCAS)
    PUBLICACION_DE_RESOLUCIONES_ANEXOS_Y_FE_DE_ERRATAS_MENSUALMENTE                              = (4, Area.PROTECCION_A_LA_PROPIEDAD_INTELECTUAL)
    PUBLICACION_DE_ACUERDOS_DIARIAMENTE                                                          = (43, Area.PROTECCION_A_LA_PROPIEDAD_INTELECTUAL)
    INVENCIONES_Y_MARCAS                                                                         = (13, Area.AREA_7)
    MARCAS_EXTINGUIDAS                                                                           = (25, Area.AREA_7)
    JURIDICO                                                                                     = (9, Area.AREA_8)
    CLASIFICACION_INTERNACIONAL_DE_PRODUCTOS_Y_SERVICIOS_PARA_EL_REGISTRO_DE_LAS_MARCAS          = (27, Area.AREA_8)
    LISTA_COMPLEMENTARIA_A_LA_CLASIFICACION_INTERNACIONAL_DE_PRODUCTOS_Y_SERVICIOS               = (28, Area.AREA_8)
    CLASIFICACION_INTERNACIONAL_DE_PRODUCTOS_Y_SERVICIOS_PARA_EL_REGISTRO_DE_LAS_MARCAS_PARTE_I  = (32, Area.AREA_8)
    CLASIFICACION_INTERNACIONAL_DE_PRODUCTOS_Y_SERVICIOS_PARA_EL_REGISTRO_DE_LAS_MARCAS_PARTE_II = (34, Area.AREA_8)


def build_url_payload(
    area: Area,
    gaceta: Gaceta | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> tuple[str, dict[str, Any]]:
    request_endpoint: str = ""

    payload: dict[str, Any] = {
        "fechaDesde": fecha_desde.strftime("%Y-%m-%d") if fecha_desde else None,
        "fechaHasta": fecha_hasta.strftime("%Y-%m-%d") if fecha_hasta else None,
        "reCaptchaToken": recaptcha,
    }
    if not gaceta and not fecha_desde and not fecha_hasta:
        message = (
            "Invalid arguments: area must be present, gaceta may be absent"
            " if fechas are present, fechas may be absent if gaceta is present"
        )
        raise ValueError(message)
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

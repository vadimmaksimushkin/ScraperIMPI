from datetime import date, timedelta
from typing import Any

import pytest

from advanced_search import build_payload, input_validation
from constants import Area, Columna, Dato, Gaceta, Operador, Seccion
from sample_data import REAL_RECAPTCHA_TOKEN

# Baseline valid case: MARCAS area, a gaceta + seccion that share the
# CLASE / EXPEDIENTE / FECHA_DE_PRESENTACION columns, and a single term.
AREA = Area.MARCAS
GACETA = Gaceta.MARCAS_REGISTRADAS_AVISOS_Y_NOMBRES_COMERCIALES
SECCION = Seccion.MARCAS_REGISTRADAS
PATENTES_GACETA = Gaceta.SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES
PATENTES_SECCION = Seccion.SOLICITUDES_DE_PATENTE  # gaceta lives in Area.PATENTES

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)


def dato_clase() -> Dato:
    return Dato(Operador.EMPTY, Columna.CLASE, "42")


def valid_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "area": AREA,
        "gacetas": [GACETA],
        "secciones": [SECCION],
        "datos": [dato_clase()],
    }
    kwargs.update(overrides)
    return kwargs


# ===========================================================================
# Valid cases
# ===========================================================================
def test_valid_full() -> None:
    ok, msg = input_validation(**valid_kwargs())
    assert ok is True
    assert msg == "OK"


def test_valid_no_secciones() -> None:
    ok, _ = input_validation(**valid_kwargs(secciones=[]))
    assert ok is True


def test_valid_none_secciones() -> None:
    ok, _ = input_validation(**valid_kwargs(secciones=None))
    assert ok is True


def test_valid_with_date_range() -> None:
    ok, _ = input_validation(**valid_kwargs(fecha_desde=LAST_WEEK, fecha_hasta=TODAY))
    assert ok is True


def test_valid_duplicate_gacetas() -> None:
    # The module's own __main__ passes duplicate gacetas; it is allowed.
    ok, _ = input_validation(**valid_kwargs(gacetas=[GACETA, GACETA], secciones=[]))
    assert ok is True


def test_valid_fecha_column_term() -> None:
    # A FECHA-kind column term (valor empty, fecha set) on a column valid for
    # both the gaceta and the seccion.
    dato = Dato(Operador.EMPTY, Columna.FECHA_DE_PRESENTACION, fecha=date(2026, 1, 1))
    ok, _ = input_validation(**valid_kwargs(datos=[dato]))
    assert ok is True


def test_valid_two_datos() -> None:
    datos = [
        Dato(Operador.EMPTY, Columna.CLASE, "42"),
        Dato(Operador.OR, Columna.EXPEDIENTE, "ZESTO"),
    ]
    ok, _ = input_validation(**valid_kwargs(datos=datos))
    assert ok is True


@pytest.mark.parametrize("operador", [Operador.AND, Operador.OR, Operador.NOT])
def test_valid_second_operador_variants(operador: Operador) -> None:
    datos = [
        Dato(Operador.EMPTY, Columna.CLASE, "42"),
        Dato(operador, Columna.EXPEDIENTE, "ZESTO"),
    ]
    ok, _ = input_validation(**valid_kwargs(datos=datos))
    assert ok is True


def test_valid_with_real_recaptcha_token() -> None:
    ok, _ = input_validation(**valid_kwargs(recaptcha=REAL_RECAPTCHA_TOKEN))
    assert ok is True


# ===========================================================================
# Dates (correct type, invalid value) + boundaries
# ===========================================================================
def test_only_fecha_desde_fails() -> None:
    ok, _ = input_validation(**valid_kwargs(fecha_desde=YESTERDAY, fecha_hasta=None))
    assert ok is False


def test_only_fecha_hasta_fails() -> None:
    ok, _ = input_validation(**valid_kwargs(fecha_desde=None, fecha_hasta=YESTERDAY))
    assert ok is False


def test_dates_out_of_order_fail() -> None:
    ok, msg = input_validation(**valid_kwargs(fecha_desde=TODAY, fecha_hasta=YESTERDAY))
    assert ok is False
    assert "<=" in msg


def test_future_fecha_hasta_fails() -> None:
    ok, msg = input_validation(**valid_kwargs(fecha_desde=TODAY, fecha_hasta=TOMORROW))
    assert ok is False
    assert "current date" in msg


def test_both_dates_in_future_fail() -> None:
    ok, _ = input_validation(
        **valid_kwargs(fecha_desde=TOMORROW, fecha_hasta=TOMORROW + timedelta(days=5))
    )
    assert ok is False


def test_today_boundary_is_allowed() -> None:
    ok, _ = input_validation(**valid_kwargs(fecha_desde=TODAY, fecha_hasta=TODAY))
    assert ok is True


# ===========================================================================
# Area / gaceta / seccion coherence
# ===========================================================================
def test_area_none_is_required() -> None:
    ok, msg = input_validation(**valid_kwargs(area=None, gacetas=None, secciones=None))
    assert ok is False
    assert "area" in msg.lower()


def test_secciones_require_gaceta() -> None:
    ok, msg = input_validation(**valid_kwargs(gacetas=[], secciones=[SECCION]))
    assert ok is False
    assert "seccion" in msg.lower()


def test_gaceta_wrong_area_fails() -> None:
    ok, msg = input_validation(**valid_kwargs(gacetas=[PATENTES_GACETA], secciones=[]))
    assert ok is False
    assert PATENTES_GACETA.area.name in msg


def test_one_gaceta_wrong_area_among_many_fails() -> None:
    ok, _ = input_validation(**valid_kwargs(gacetas=[GACETA, PATENTES_GACETA], secciones=[]))
    assert ok is False


def test_seccion_wrong_area_fails() -> None:
    ok, _ = input_validation(**valid_kwargs(gacetas=[GACETA], secciones=[PATENTES_SECCION]))
    assert ok is False


# ===========================================================================
# Datos (correct type, invalid value) + boundaries
# ===========================================================================
def test_empty_datos_fails() -> None:
    ok, msg = input_validation(**valid_kwargs(datos=[]))
    assert ok is False
    assert "at least one" in msg


def test_three_datos_fails() -> None:
    datos = [
        Dato(Operador.EMPTY, Columna.CLASE, "42"),
        Dato(Operador.OR, Columna.EXPEDIENTE, "ZESTO"),
        Dato(Operador.AND, Columna.DENOMINACION, "X"),
    ]
    ok, msg = input_validation(**valid_kwargs(datos=datos))
    assert ok is False
    assert "at most two" in msg


@pytest.mark.parametrize("operador", [Operador.AND, Operador.OR, Operador.NOT])
def test_first_dato_must_have_empty_operador(operador: Operador) -> None:
    datos = [Dato(operador, Columna.CLASE, "42")]
    ok, msg = input_validation(**valid_kwargs(datos=datos))
    assert ok is False
    assert "empty operador" in msg


def test_second_dato_must_have_operador() -> None:
    datos = [
        Dato(Operador.EMPTY, Columna.CLASE, "42"),
        Dato(Operador.EMPTY, Columna.EXPEDIENTE, "ZESTO"),
    ]
    ok, msg = input_validation(**valid_kwargs(datos=datos))
    assert ok is False
    assert "non-empty operador" in msg


# ===========================================================================
# Column validity
# ===========================================================================
def test_column_invalid_for_gaceta() -> None:
    # PATENTE is a real column but not part of the MARCAS_REGISTRADAS gaceta.
    datos = [Dato(Operador.EMPTY, Columna.PATENTE, "X")]
    ok, msg = input_validation(**valid_kwargs(secciones=[], datos=datos))
    assert ok is False
    assert "not a valid column" in msg
    assert "gaceta" in msg


def test_column_invalid_for_seccion() -> None:
    # DEBE_DECIR is valid for the gaceta but not the MARCAS_REGISTRADAS seccion.
    datos = [Dato(Operador.EMPTY, Columna.DEBE_DECIR, "X")]
    ok, msg = input_validation(**valid_kwargs(datos=datos))
    assert ok is False
    assert "not a valid column" in msg
    assert "seccion" in msg


# ===========================================================================
# recaptcha type validation
# ===========================================================================
@pytest.mark.parametrize("bad", [123, 0, None, b"token", 1.5, ["t"], {"t": 1}])
def test_recaptcha_must_be_str(bad: object) -> None:
    ok, msg = input_validation(**valid_kwargs(recaptcha=bad))  # type: ignore[arg-type]
    assert ok is False
    assert "recaptcha" in msg


def test_recaptcha_empty_string_ok() -> None:
    ok, _ = input_validation(**valid_kwargs(recaptcha=""))
    assert ok is True


@pytest.mark.parametrize(
    "bad",
    ["abc", "x" * 19, "has spaces in this token here now", "bad!chars#in$token"],
)
def test_recaptcha_invalid_format_rejected(bad: str) -> None:
    ok, msg = input_validation(**valid_kwargs(recaptcha=bad))
    assert ok is False
    assert "format" in msg


def test_recaptcha_minimal_valid_format_accepted() -> None:
    ok, _ = input_validation(**valid_kwargs(recaptcha="A" * 20))
    assert ok is True


# ===========================================================================
# Type validation contract — wrong types must be REJECTED as (False, message).
# These assert the INTENDED behavior; they currently FAIL/ERROR because
# input_validation does not yet type-check these params (hardening = Option A).
# ===========================================================================
def test_area_wrong_type_str_rejected() -> None:
    ok, _ = input_validation(area="MARCAS", gacetas=None, secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]
    assert ok is False


def test_area_wrong_type_int_rejected() -> None:
    ok, _ = input_validation(area=2, gacetas=None, secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]
    assert ok is False


def test_area_wrong_type_with_gacetas_rejected() -> None:
    ok, _ = input_validation(area="MARCAS", gacetas=[GACETA], secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]
    assert ok is False


def test_gacetas_list_of_wrong_type_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=[1, 2], secciones=None, datos=[dato_clase()])  # type: ignore[list-item]
    assert ok is False


def test_gacetas_str_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas="ab", secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]
    assert ok is False


def test_gacetas_non_iterable_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=123, secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]
    assert ok is False


def test_secciones_list_of_wrong_type_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=[GACETA], secciones=[1], datos=[dato_clase()])  # type: ignore[list-item]
    assert ok is False


def test_datos_str_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=None, secciones=None, datos="x")  # type: ignore[arg-type]
    assert ok is False


def test_datos_list_of_wrong_type_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=None, secciones=None, datos=[1])  # type: ignore[list-item]
    assert ok is False


def test_datos_non_sized_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=None, secciones=None, datos=123)  # type: ignore[arg-type]
    assert ok is False


def test_datos_none_rejected() -> None:
    ok, _ = input_validation(area=AREA, gacetas=None, secciones=None, datos=None)  # type: ignore[arg-type]
    assert ok is False


def test_fecha_as_str_rejected() -> None:
    ok, _ = input_validation(  # type: ignore[arg-type]
        area=AREA, gacetas=None, secciones=None, datos=[dato_clase()],
        fecha_desde="2026-01-01", fecha_hasta="2026-02-01",
    )
    assert ok is False


# ===========================================================================
# build_payload: wiring + payload shape
# ===========================================================================
def test_build_payload_shape() -> None:
    payload = build_payload(
        area=AREA,
        gacetas=[GACETA],
        secciones=[SECCION],
        datos=[Dato(Operador.EMPTY, Columna.CLASE, "42")],
        fecha_desde=date(2026, 1, 5),
        fecha_hasta=date(2026, 2, 5),
        recaptcha=REAL_RECAPTCHA_TOKEN,
    )
    assert payload["idArea"] == str(AREA.value)
    assert payload["idGaceta"] == [GACETA.id_gaceta]
    assert payload["idSeccion"] == [SECCION.id_seccion]
    # advanced uses day-first formatting (contrast with copies' %Y-%m-%d).
    assert payload["FechaDesde"] == "05-01-2026"
    assert payload["FechaHasta"] == "05-02-2026"
    assert payload["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN
    assert payload["datos"] == [
        {"operador": "", "columna": "Clase", "valor": "42", "fecha": ""}
    ]


def test_build_payload_empty_optionals() -> None:
    payload = build_payload(
        area=AREA, gacetas=[GACETA], secciones=[], datos=[dato_clase()]
    )
    assert payload["idSeccion"] == []
    assert payload["FechaDesde"] == ""
    assert payload["FechaHasta"] == ""
    assert payload["reCaptchaToken"] == ""


def test_build_payload_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_payload(area=AREA, gacetas=[GACETA], secciones=[], datos=[])


def test_build_payload_wrong_type_area_raises_value_error() -> None:
    # Once validation rejects bad types, build should raise ValueError.
    with pytest.raises(ValueError):
        build_payload(area=2, gacetas=None, secciones=None, datos=[dato_clase()])  # type: ignore[arg-type]

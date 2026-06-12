from datetime import date, timedelta

import pytest

from constants import Area, Gaceta
from records_search import build_payload, input_validation
from sample_data import REAL_RECAPTCHA_TOKEN

BUSQUEDA = 3618676
AREA = Area.MARCAS
GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
GACETA_2 = Gaceta.SOLICITUDES_DE_MARCAS_NOMBRES_COMERCIALES_Y_AVISOS_COMERCIALES_ABANDONADAS
PATENTES_AREA = Area.PATENTES
PATENTES_GACETA = Gaceta.SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)


# ===========================================================================
# Valid cases
# ===========================================================================
def test_valid_busqueda_only() -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA)
    assert ok is True
    assert msg == "OK"


def test_valid_with_area_and_gacetas() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[GACETA])
    assert ok is True


def test_valid_with_two_gacetas_same_area() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[GACETA, GACETA_2])
    assert ok is True


def test_valid_patentes_area() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=PATENTES_AREA, gacetas=[PATENTES_GACETA])
    assert ok is True


def test_valid_with_dates() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, fecha_desde=LAST_WEEK, fecha_hasta=TODAY)
    assert ok is True


def test_valid_with_real_recaptcha_token() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, recaptcha=REAL_RECAPTCHA_TOKEN)
    assert ok is True


# ===========================================================================
# busqueda: type validation (this param IS type-checked)
# ===========================================================================
@pytest.mark.parametrize("bad", ["3618676", 10.0, None, b"10", [10]])
def test_busqueda_wrong_type_rejected(bad: object) -> None:
    ok, msg = input_validation(busqueda=bad)  # type: ignore[arg-type]
    assert ok is False
    assert "int" in msg


# ===========================================================================
# busqueda: value validation (>= 10 / at least 2 digits) + boundaries
# ===========================================================================
@pytest.mark.parametrize("value", [10, 11, 99, 100, BUSQUEDA])
def test_busqueda_valid_values(value: int) -> None:
    ok, _ = input_validation(busqueda=value)
    assert ok is True


@pytest.mark.parametrize("value", [9, 1, 0, -5, -100])
def test_busqueda_too_short_or_small_rejected(value: int) -> None:
    ok, msg = input_validation(busqueda=value)
    assert ok is False
    assert "2 digits" in msg


@pytest.mark.parametrize("value", [True, False])
def test_busqueda_bool_rejected_as_too_short(value: bool) -> None:
    # bool is an int subclass, so it passes isinstance; values 1/0 are < 10.
    ok, _ = input_validation(busqueda=value)  # type: ignore[arg-type]
    assert ok is False


# ===========================================================================
# area / gacetas coherence
# ===========================================================================
def test_area_without_gacetas_fails() -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=None)
    assert ok is False
    assert "both present or absent" in msg


def test_gacetas_without_area_fails() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=None, gacetas=[GACETA])
    assert ok is False


def test_empty_gacetas_with_area_fails() -> None:
    # [] is falsy, so this is treated as "area present, gacetas absent".
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[])
    assert ok is False


def test_gacetas_must_match_area() -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[PATENTES_GACETA])
    assert ok is False
    assert "same Area" in msg


def test_one_gaceta_mismatched_area_fails() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[GACETA, PATENTES_GACETA])
    assert ok is False


# ===========================================================================
# dates (correct type, invalid value) + boundaries
# ===========================================================================
def test_only_fecha_desde_fails() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, fecha_desde=YESTERDAY)
    assert ok is False


def test_dates_out_of_order_fail() -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, fecha_desde=TODAY, fecha_hasta=YESTERDAY)
    assert ok is False
    assert "<=" in msg


def test_future_date_fails() -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, fecha_desde=TODAY, fecha_hasta=TOMORROW)
    assert ok is False
    assert "current date" in msg


def test_today_boundary_is_allowed() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, fecha_desde=TODAY, fecha_hasta=TODAY)
    assert ok is True


@pytest.mark.parametrize("bad", [123, None, b"t", 1.5])
def test_recaptcha_must_be_str(bad: object) -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, recaptcha=bad)  # type: ignore[arg-type]
    assert ok is False
    assert "recaptcha" in msg


@pytest.mark.parametrize(
    "bad",
    ["abc", "x" * 19, "has spaces in this token here now", "bad!chars#in$token"],
)
def test_recaptcha_invalid_format_rejected(bad: str) -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, recaptcha=bad)
    assert ok is False
    assert "format" in msg


# ===========================================================================
# Type validation contract — wrong types must be REJECTED as (False, message).
# These assert the INTENDED behavior; they currently FAIL/ERROR because
# input_validation does not yet type-check these params (hardening = Option A).
# ===========================================================================
def test_area_wrong_type_with_gacetas_rejected() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area="x", gacetas=[GACETA])  # type: ignore[arg-type]
    assert ok is False


def test_gacetas_list_of_wrong_type_rejected() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas=[1])  # type: ignore[list-item]
    assert ok is False


def test_gacetas_str_rejected() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, area=AREA, gacetas="ab")  # type: ignore[arg-type]
    assert ok is False


def test_fecha_as_str_rejected() -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, fecha_desde="2026-01-01", fecha_hasta="2026-02-01")  # type: ignore[arg-type]
    assert ok is False


# ===========================================================================
# build_payload: wiring + payload shape
# ===========================================================================
def test_build_payload_shape() -> None:
    payload = build_payload(
        busqueda=BUSQUEDA,
        area=AREA,
        gacetas=[GACETA, GACETA_2],
        fecha_desde=date(2026, 1, 5),
        fecha_hasta=date(2026, 2, 9),
        recaptcha=REAL_RECAPTCHA_TOKEN,
    )
    assert payload["busqueda"] == str(BUSQUEDA)
    assert payload["idArea"] == str(AREA.value)
    assert payload["idGaceta"] == [GACETA.id_gaceta, GACETA_2.id_gaceta]
    # records uses day-first formatting (contrast with copies' %Y-%m-%d).
    assert payload["fechaDesde"] == "05-01-2026"
    assert payload["fechaHasta"] == "09-02-2026"
    assert payload["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN


def test_build_payload_empty_optionals() -> None:
    payload = build_payload(busqueda=BUSQUEDA)
    assert payload["busqueda"] == str(BUSQUEDA)
    assert payload["idArea"] == ""
    assert payload["idGaceta"] == []
    assert payload["fechaDesde"] == ""
    assert payload["fechaHasta"] == ""
    assert payload["reCaptchaToken"] == ""


def test_build_payload_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_payload(busqueda=9)

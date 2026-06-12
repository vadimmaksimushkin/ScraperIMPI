from datetime import date, timedelta

import pytest

from constants import Area, Gaceta
from copies_search import (
    URL_BY_FECHA,
    URL_BY_GACETA,
    build_url_payload,
    input_validation,
)
from sample_data import REAL_RECAPTCHA_TOKEN

AREA = Area.MARCAS
GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
# A gaceta in a *different* area than AREA, to probe area handling.
PATENTES_GACETA = Gaceta.SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
LAST_WEEK = TODAY - timedelta(days=7)


# ===========================================================================
# input_validation: valid cases
# ===========================================================================
def test_valid_gaceta_only() -> None:
    ok, msg = input_validation(area=AREA, gaceta=GACETA)
    assert ok is True
    assert msg == "OK"


def test_valid_dates_only() -> None:
    ok, _ = input_validation(area=AREA, fecha_desde=LAST_WEEK, fecha_hasta=TODAY)
    assert ok is True


def test_valid_gaceta_and_dates() -> None:
    ok, _ = input_validation(
        area=AREA, gaceta=GACETA, fecha_desde=LAST_WEEK, fecha_hasta=TODAY
    )
    assert ok is True


def test_valid_with_real_recaptcha_token() -> None:
    ok, _ = input_validation(area=AREA, gaceta=GACETA, recaptcha=REAL_RECAPTCHA_TOKEN)
    assert ok is True


# ===========================================================================
# input_validation: invalid values + boundaries
# ===========================================================================
def test_area_none_is_required() -> None:
    ok, msg = input_validation(area=None, gaceta=GACETA)  # type: ignore[arg-type]
    assert ok is False
    assert "area" in msg.lower()


def test_needs_gaceta_or_dates() -> None:
    ok, msg = input_validation(area=AREA)
    assert ok is False
    assert "gaceta" in msg


def test_only_fecha_desde_fails() -> None:
    ok, _ = input_validation(area=AREA, fecha_desde=YESTERDAY)
    assert ok is False


def test_only_fecha_hasta_fails() -> None:
    ok, _ = input_validation(area=AREA, fecha_hasta=YESTERDAY)
    assert ok is False


def test_dates_out_of_order_fail() -> None:
    ok, msg = input_validation(area=AREA, fecha_desde=TODAY, fecha_hasta=YESTERDAY)
    assert ok is False
    assert "<=" in msg


def test_future_date_fails() -> None:
    ok, msg = input_validation(area=AREA, fecha_desde=TODAY, fecha_hasta=TOMORROW)
    assert ok is False
    assert "current date" in msg


def test_today_boundary_is_allowed() -> None:
    ok, _ = input_validation(area=AREA, fecha_desde=TODAY, fecha_hasta=TODAY)
    assert ok is True


@pytest.mark.parametrize("bad", [123, 0, None, b"token", 1.5, ["t"]])
def test_recaptcha_must_be_str(bad: object) -> None:
    ok, msg = input_validation(area=AREA, gaceta=GACETA, recaptcha=bad)  # type: ignore[arg-type]
    assert ok is False
    assert "recaptcha" in msg


@pytest.mark.parametrize(
    "bad",
    ["abc", "x" * 19, "has spaces in this token here now", "bad!chars#in$token"],
)
def test_recaptcha_invalid_format_rejected(bad: str) -> None:
    ok, msg = input_validation(area=AREA, gaceta=GACETA, recaptcha=bad)
    assert ok is False
    assert "format" in msg


# ===========================================================================
# Type validation contract — wrong types must be REJECTED as (False, message).
# These assert the INTENDED behavior; they currently FAIL/ERROR because
# input_validation does not yet type-check these params (hardening = Option A).
# ===========================================================================
def test_area_wrong_type_rejected() -> None:
    ok, _ = input_validation(area="x", gaceta=GACETA)  # type: ignore[arg-type]
    assert ok is False


def test_gaceta_wrong_type_str_rejected() -> None:
    ok, _ = input_validation(area=AREA, gaceta="x")  # type: ignore[arg-type]
    assert ok is False


def test_gaceta_wrong_type_int_rejected() -> None:
    ok, _ = input_validation(area=AREA, gaceta=123)  # type: ignore[arg-type]
    assert ok is False


def test_fecha_as_str_rejected() -> None:
    ok, _ = input_validation(area=AREA, fecha_desde="2026-01-01", fecha_hasta="2026-02-01")  # type: ignore[arg-type]
    assert ok is False


# ===========================================================================
# build_url_payload: the two endpoints + payload shape
# ===========================================================================
def test_url_by_gaceta_selected() -> None:
    url, payload = build_url_payload(area=AREA, gaceta=GACETA)
    assert url == URL_BY_GACETA
    assert payload["idGaceta"] == str(GACETA.id_gaceta)
    assert payload["idArea"] == str(GACETA.area.value)
    assert payload["fechaDesde"] is None
    assert payload["fechaHasta"] is None
    assert payload["reCaptchaToken"] == ""


def test_url_by_fecha_selected() -> None:
    url, payload = build_url_payload(
        area=AREA, fecha_desde=date(2026, 1, 5), fecha_hasta=date(2026, 2, 9)
    )
    assert url == URL_BY_FECHA
    assert payload["idGaceta"] is None
    assert payload["idArea"] == str(AREA.value)
    # copies uses ISO year-first formatting (contrast with %d-%m-%Y elsewhere).
    assert payload["fechaDesde"] == "2026-01-05"
    assert payload["fechaHasta"] == "2026-02-09"


def test_gaceta_wins_over_fecha() -> None:
    # When both gaceta and dates are given, the by-gaceta endpoint is used,
    # but the date fields are still populated in the payload.
    url, payload = build_url_payload(
        area=AREA, gaceta=GACETA, fecha_desde=date(2026, 1, 5), fecha_hasta=date(2026, 2, 9)
    )
    assert url == URL_BY_GACETA
    assert payload["idGaceta"] == str(GACETA.id_gaceta)
    assert payload["fechaDesde"] == "2026-01-05"
    assert payload["fechaHasta"] == "2026-02-09"


def test_idarea_follows_gaceta_not_passed_area() -> None:
    # copies does NOT check area<->gaceta coherence: with a gaceta present the
    # passed `area` is ignored and idArea comes from gaceta.area. Pass a
    # mismatching area (PATENTES) and confirm idArea is the gaceta's (MARCAS).
    _, payload = build_url_payload(area=Area.PATENTES, gaceta=GACETA)
    assert payload["idArea"] == str(GACETA.area.value)
    assert payload["idArea"] != str(Area.PATENTES.value)


def test_recaptcha_passthrough_in_payload() -> None:
    _, payload = build_url_payload(area=AREA, gaceta=GACETA, recaptcha=REAL_RECAPTCHA_TOKEN)
    assert payload["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN


def test_build_url_payload_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_url_payload(area=AREA)


def test_build_url_payload_gaceta_wrong_type_raises_value_error() -> None:
    # Once validation rejects bad types, build should raise ValueError.
    with pytest.raises(ValueError):
        build_url_payload(area=AREA, gaceta="x")  # type: ignore[arg-type]


def test_build_url_payload_area_wrong_type_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_url_payload(area="bogus", gaceta=GACETA)  # type: ignore[arg-type]

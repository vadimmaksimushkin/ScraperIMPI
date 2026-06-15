"""Adversarial tests for records_search — the very-hard companion to
test_records_search.py (which covers input_validation / build_payload basics).
This file hammers the is_list_of helper, adds the async search() coroutine
(otherwise untested), and probes the date/datetime trap.

records_search is the more hardened module (it rejects bool busqueda and DOES
enforce area<->gaceta coherence), so there are fewer genuine defects than in
copies/home_download. The INTENDED-behaviour section that currently FAILS/ERRORS
documents the one real trap it shares: a datetime slips past isinstance(date)
and crashes the future-date comparison with TypeError.

No network: request_with_token is monkeypatched. Coroutines run via
asyncio.run() because pytest-asyncio is not installed.
"""

import asyncio
from datetime import date, datetime, timedelta

import pytest

import records_search
from constants import Area, Gaceta, RequestMethods
from records_search import (
    URL,
    build_payload,
    input_validation,
    is_list_of,
    search,
)
from sample_data import REAL_RECAPTCHA_TOKEN

BUSQUEDA = 3618676
AREA = Area.MARCAS
GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
GACETA_2 = Gaceta.SOLICITUDES_DE_MARCAS_NOMBRES_COMERCIALES_Y_AVISOS_COMERCIALES_ABANDONADAS
PATENTES_GACETA = Gaceta.SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES
# A gaceta whose .area is one of the "DOES NOT EXIST" sentinel areas (AREA_7).
AREA_7_GACETA = Gaceta.INVENCIONES_Y_MARCAS

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)

# search() never touches the session itself — it only forwards it.
SESSION = object()


def run(coro):
    return asyncio.run(coro)


def _patch_rwt(monkeypatch, status: int = 200, res=None) -> dict:
    """Stub records_search.request_with_token; return a dict capturing kwargs."""
    captured: dict = {}

    async def fake_rwt(session, method, url, payload):
        captured.update(session=session, method=method, url=url, payload=payload)
        return status, ({"data": []} if res is None else res)

    monkeypatch.setattr(records_search, "request_with_token", fake_rwt)
    return captured


# ===========================================================================
# is_list_of: the type-guard helper (should PASS)
# ===========================================================================
def test_is_list_of_valid() -> None:
    assert is_list_of([GACETA, GACETA_2], Gaceta) is True


def test_is_list_of_empty_is_vacuously_true() -> None:
    # all() over no items is True: [] "is a list of Gaceta".
    assert is_list_of([], Gaceta) is True


def test_is_list_of_wrong_element_rejected() -> None:
    assert is_list_of([GACETA, "x"], Gaceta) is False


@pytest.mark.parametrize(
    "not_a_list", ["abc", (GACETA,), {GACETA}, None, GACETA, 123]
)
def test_is_list_of_non_list_rejected(not_a_list) -> None:
    assert is_list_of(not_a_list, Gaceta) is False


def test_is_list_of_string_not_walked_char_by_char() -> None:
    # The isinstance(list) guard stops a str from being iterated char-by-char.
    assert is_list_of("not a list", Gaceta) is False


def test_is_list_of_respects_the_type_arg() -> None:
    assert is_list_of([Area.MARCAS, Area.PATENTES], Area) is True
    assert is_list_of([Area.MARCAS, GACETA], Area) is False


# ===========================================================================
# search(): wiring (should PASS)
# ===========================================================================
def test_search_busqueda_only_wires_request(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch, 200, {"data": ["x"]})
    status, res = run(search(SESSION, BUSQUEDA))
    assert (status, res) == (200, {"data": ["x"]})
    assert cap["session"] is SESSION
    assert cap["method"] == RequestMethods.POST
    assert cap["url"] == URL
    assert cap["payload"]["busqueda"] == str(BUSQUEDA)
    assert cap["payload"]["idArea"] == ""
    assert cap["payload"]["idGaceta"] == []


def test_search_full_payload(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch)
    run(
        search(
            SESSION,
            BUSQUEDA,
            area=AREA,
            gacetas=[GACETA, GACETA_2],
            fecha_desde=date(2026, 1, 5),
            fecha_hasta=date(2026, 2, 9),
            recaptcha=REAL_RECAPTCHA_TOKEN,
        )
    )
    p = cap["payload"]
    assert p["idArea"] == str(AREA.value)
    assert p["idGaceta"] == [GACETA.id_gaceta, GACETA_2.id_gaceta]
    # records uses day-first formatting (contrast with copies' %Y-%m-%d).
    assert p["fechaDesde"] == "05-01-2026"
    assert p["fechaHasta"] == "09-02-2026"
    assert p["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN


def test_search_returns_non_200_without_raising(monkeypatch) -> None:
    _patch_rwt(monkeypatch, 500, "kaboom")
    status, res = run(search(SESSION, BUSQUEDA))
    assert status == 500
    assert res == "kaboom"


def test_search_invalid_raises_before_request(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake(**kwargs):
        calls["n"] += 1
        return 200, {}

    monkeypatch.setattr(records_search, "request_with_token", fake)
    with pytest.raises(ValueError):
        run(search(SESSION, 9))  # busqueda too short
    assert calls["n"] == 0


# ===========================================================================
# build_payload: harder shape cases (should PASS)
# ===========================================================================
def test_build_payload_preserves_gaceta_order() -> None:
    # The list-comprehension keeps caller order (contrast home_download's set).
    p = build_payload(busqueda=BUSQUEDA, area=AREA, gacetas=[GACETA_2, GACETA])
    assert p["idGaceta"] == [GACETA_2.id_gaceta, GACETA.id_gaceta]


def test_build_payload_keeps_duplicate_gacetas() -> None:
    p = build_payload(busqueda=BUSQUEDA, area=AREA, gacetas=[GACETA, GACETA])
    assert p["idGaceta"] == [GACETA.id_gaceta, GACETA.id_gaceta]


def test_build_payload_busqueda_is_stringified() -> None:
    p = build_payload(busqueda=BUSQUEDA)
    assert p["busqueda"] == str(BUSQUEDA)
    assert isinstance(p["busqueda"], str)


# ===========================================================================
# coherence quirk + recaptcha boundary (should PASS)
# ===========================================================================
def test_nonexistent_area_accepted_if_gaceta_matches() -> None:
    # A gaceta in AREA_7 ('DOES NOT EXIST') validates if you pass Area.AREA_7,
    # because coherence is checked by .value equality, not by area existence.
    ok, _ = input_validation(busqueda=BUSQUEDA, area=Area.AREA_7, gacetas=[AREA_7_GACETA])
    assert ok is True
    p = build_payload(busqueda=BUSQUEDA, area=Area.AREA_7, gacetas=[AREA_7_GACETA])
    assert p["idArea"] == str(Area.AREA_7.value)  # "7"


@pytest.mark.parametrize("tok", ["a" * 20, "A_-9" * 5, REAL_RECAPTCHA_TOKEN])
def test_recaptcha_valid_boundary(tok: str) -> None:
    ok, _ = input_validation(busqueda=BUSQUEDA, recaptcha=tok)
    assert ok is True


@pytest.mark.parametrize(
    "tok", ["a" * 19, "a" * 20 + "!", "two words here now", "🚀" * 20]
)
def test_recaptcha_bad(tok: str) -> None:
    ok, msg = input_validation(busqueda=BUSQUEDA, recaptcha=tok)
    assert ok is False
    assert "format" in msg


# ===========================================================================
# datetime is floored to a pure date (time-of-day is meaningless here).
# ===========================================================================
@pytest.mark.parametrize(
    "desde, hasta",
    [
        (datetime(2020, 1, 1, 12, 0), datetime(2020, 1, 1, 12, 0)),
        (date(2020, 1, 1), datetime(2020, 1, 1, 12, 0)),
    ],
    ids=["both-datetime", "date-and-datetime"],
)
def test_datetime_for_fecha_normalized_cleanly(desde, hasta) -> None:
    # datetime subclasses date; it's floored to its date instead of raising a
    # TypeError on the `> mexico_today()` compare, so a past datetime validates.
    ok, _ = input_validation(busqueda=BUSQUEDA, fecha_desde=desde, fecha_hasta=hasta)  # type: ignore[arg-type]
    assert ok is True


def test_build_datetime_normalized_to_date() -> None:
    # A datetime is floored to its date; build_payload serializes the date part.
    payload = build_payload(
        busqueda=BUSQUEDA,
        fecha_desde=datetime(2020, 1, 1),  # type: ignore[arg-type]
        fecha_hasta=datetime(2020, 1, 1),  # type: ignore[arg-type]
    )
    assert payload["fechaDesde"] == "01-01-2020"
    assert payload["fechaHasta"] == "01-01-2020"

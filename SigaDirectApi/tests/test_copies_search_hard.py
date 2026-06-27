"""Adversarial tests for copies_search — the very-hard companion to
test_copies_search.py (which covers the input_validation / build_url_payload
basics). This file pushes harder and adds the async search() coroutine, which
is otherwise untested.

Several sections assert the *intended* behaviour and therefore currently
FAIL/ERROR — they document real traps: a datetime sails past the date gate and
crashes the future-date comparison with TypeError, and copies (unlike
records_search) never checks area<->gaceta coherence. Deliberate, mirroring the
hardening contract tests elsewhere.

No network: request_with_token is monkeypatched. Coroutines run via
asyncio.run() because pytest-asyncio is not installed.
"""

import asyncio
from datetime import date, datetime, timedelta

import pytest

import copies_search
from constants import Area, Gaceta, RequestMethods
from copies_search import (
    URL_BY_FECHA,
    URL_BY_GACETA,
    build_url_payload,
    input_validation,
    search,
)
from sample_data import REAL_RECAPTCHA_TOKEN

AREA = Area.MARCAS
GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
# A gaceta whose .area is one of the "DOES NOT EXIST" sentinel areas (AREA_7).
FAKE_AREA_GACETA = Gaceta.INVENCIONES_Y_MARCAS

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)

# search() never touches the session itself — it only forwards it.
SESSION = object()


def run(coro):
    return asyncio.run(coro)


def _patch_rwt(monkeypatch, status: int = 200, res=None) -> dict:
    """Stub copies_search.request_with_token; return a dict that captures the
    kwargs it was called with."""
    captured: dict = {}

    async def fake_rwt(session, method, url, payload, token=None):
        captured.update(
            session=session, method=method, url=url, payload=payload, token=token
        )
        return status, ({"data": []} if res is None else res)

    monkeypatch.setattr(copies_search, "request_with_token", fake_rwt)
    return captured


# ===========================================================================
# search(): wiring (should PASS)
# ===========================================================================
def test_search_by_gaceta_wires_request(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch, status=200, res={"data": [1, 2, 3]})
    status, res = run(search(SESSION, AREA, gaceta=GACETA))
    assert (status, res) == (200, {"data": [1, 2, 3]})
    assert cap["session"] is SESSION
    assert cap["method"] == RequestMethods.POST
    assert cap["url"] == URL_BY_GACETA
    assert cap["payload"]["idGaceta"] == str(GACETA.id_gaceta)
    assert cap["payload"]["idArea"] == str(GACETA.area.value)


def test_search_by_fecha_uses_fecha_endpoint(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch)
    run(search(SESSION, AREA, fecha_desde=date(2026, 1, 5), fecha_hasta=date(2026, 2, 9)))
    assert cap["url"] == URL_BY_FECHA
    assert cap["payload"]["fechaDesde"] == "2026-01-05"
    assert cap["payload"]["fechaHasta"] == "2026-02-09"
    assert cap["payload"]["idGaceta"] is None


def test_search_forwards_recaptcha(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch)
    run(search(SESSION, AREA, gaceta=GACETA, recaptcha=REAL_RECAPTCHA_TOKEN))
    assert cap["payload"]["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN


def test_search_returns_non_200_without_raising(monkeypatch) -> None:
    # search() does not raise on upstream errors; it returns the status/body.
    _patch_rwt(monkeypatch, status=503, res="upstream boom")
    status, res = run(search(SESSION, AREA, gaceta=GACETA))
    assert status == 503
    assert res == "upstream boom"


def test_search_invalid_input_raises_before_request(monkeypatch) -> None:
    # An invalid call must raise ValueError from build_url_payload and never
    # reach the network.
    calls = {"n": 0}

    async def fake_rwt(**kwargs):
        calls["n"] += 1
        return 200, {}

    monkeypatch.setattr(copies_search, "request_with_token", fake_rwt)
    with pytest.raises(ValueError):
        run(search(SESSION, AREA))  # no gaceta, no dates
    assert calls["n"] == 0


# ===========================================================================
# input_validation: harder valid/invalid cases (should PASS)
# ===========================================================================
@pytest.mark.parametrize("tok", ["a" * 20, "A_-9" * 5, REAL_RECAPTCHA_TOKEN])
def test_recaptcha_valid_boundary_accepted(tok: str) -> None:
    ok, _ = input_validation(area=AREA, gaceta=GACETA, recaptcha=tok)
    assert ok is True


@pytest.mark.parametrize(
    "tok", ["a" * 19, "a" * 20 + "!", "abc def ghij klmno", "🚀" * 20]
)
def test_recaptcha_bad_rejected(tok: str) -> None:
    ok, msg = input_validation(area=AREA, gaceta=GACETA, recaptcha=tok)
    assert ok is False
    assert "format" in msg


def test_bool_gaceta_rejected() -> None:
    ok, msg = input_validation(area=AREA, gaceta=True)  # type: ignore[arg-type]
    assert ok is False
    assert "gaceta" in msg


def test_recaptcha_alone_is_insufficient() -> None:
    # A token doesn't scope a search; you still need a gaceta or a date range.
    ok, msg = input_validation(area=AREA, recaptcha=REAL_RECAPTCHA_TOKEN)
    assert ok is False
    assert "gaceta" in msg


def test_equal_dates_in_the_past_ok() -> None:
    ok, _ = input_validation(area=AREA, fecha_desde=YESTERDAY, fecha_hasta=YESTERDAY)
    assert ok is True


# ===========================================================================
# build_url_payload: quirks (should PASS)
# ===========================================================================
def test_build_emits_nonexistent_area_code() -> None:
    # A gaceta mapped to a 'DOES NOT EXIST' sentinel area (AREA_7) still yields
    # its code on the wire. Area must now match the gaceta's area (coherence).
    _, payload = build_url_payload(area=Area.AREA_7, gaceta=FAKE_AREA_GACETA)
    assert payload["idArea"] == str(Area.AREA_7.value)  # "7"
    assert payload["idGaceta"] == str(FAKE_AREA_GACETA.id_gaceta)


def test_build_equal_dates_in_past() -> None:
    url, payload = build_url_payload(
        area=AREA, fecha_desde=date(2025, 1, 1), fecha_hasta=date(2025, 1, 1)
    )
    assert url == URL_BY_FECHA
    assert payload["fechaDesde"] == payload["fechaHasta"] == "2025-01-01"


# ===========================================================================
# INTENDED behaviour — now enforced.
# ===========================================================================
def test_datetime_for_fecha_normalized_cleanly() -> None:
    # A datetime subclasses date; it's floored to its date instead of raising a
    # TypeError on the `> mexico_today()` compare, so a past datetime validates.
    ok, _ = input_validation(
        area=AREA,
        fecha_desde=datetime(2020, 1, 1, 12, 0),  # type: ignore[arg-type]
        fecha_hasta=datetime(2020, 1, 1, 12, 0),  # type: ignore[arg-type]
    )
    assert ok is True


def test_build_datetime_normalized_to_date() -> None:
    # A datetime is floored to its date; build serializes the date part.
    _, payload = build_url_payload(
        area=AREA,
        fecha_desde=datetime(2020, 1, 1),  # type: ignore[arg-type]
        fecha_hasta=datetime(2020, 1, 1),  # type: ignore[arg-type]
    )
    assert payload["fechaDesde"] == payload["fechaHasta"] == "2020-01-01"


def test_area_gaceta_mismatch_should_be_rejected() -> None:
    # INTENDED (parity with records_search, which checks coherence): a gaceta
    # from a different area than `area` should be rejected. copies ignores
    # `area` when a gaceta is present, so this currently passes validation.
    ok, _ = input_validation(area=Area.PATENTES, gaceta=GACETA)  # GACETA is MARCAS
    assert ok is False

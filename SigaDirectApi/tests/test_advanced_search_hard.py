"""Adversarial tests for advanced_search — the very-hard companion to
test_advanced_search.py. This is the most complex module: structured search
with area/gaceta/seccion coherence and per-column searchability checks across
the GACETA_COLUMNAS / SECCION_COLUMNAS junction maps. It also covers the async
search() coroutine and the Dato payload serialization that build_payload relies
on.

The INTENDED-behaviour section currently FAILS/ERRORS and documents real traps:
the shared datetime-past-the-date-gate TypeError, and an index asymmetry — a
gaceta missing from its map degrades gracefully (`GACETA_COLUMNAS.get(g, set())`)
but a seccion missing from its map crashes with KeyError (`SECCION_COLUMNAS[s]`).
The maps are hand-maintained (see the FIXME in constants.py), so that asymmetry
is a genuine fragility.

No network: request_with_token is monkeypatched. Coroutines run via
asyncio.run() because pytest-asyncio is not installed.
"""

import asyncio
from datetime import date, datetime, timedelta

import pytest

import advanced_search
from advanced_search import (
    URL,
    build_payload,
    input_validation,
    is_list_of,
    search,
)
from constants import (
    Area,
    Columna,
    Dato,
    Gaceta,
    Operador,
    RequestMethods,
    Seccion,
)
from sample_data import REAL_RECAPTCHA_TOKEN

AREA = Area.MARCAS
# Gaceta 35 — searchable columns: {EXPEDIENTE, CLASE, FECHA_DE_PRESENTACION}.
GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO
# Seccion 100188 of GACETA — cols {CLASE, DENOMINACION, EXPEDIENTE, FECHA_DE_PRESENTACION}.
SECCION = Seccion.SOLICITUDES_DE_MARCAS_100188

# A broad gaceta + one of its narrow secciones: a column can pass the gaceta
# check yet fail the seccion check.
BROAD_GACETA = Gaceta.MARCAS_REGISTRADAS_AVISOS_Y_NOMBRES_COMERCIALES  # has EXPEDIENTE
NARROW_SECCION = Seccion.FE_DE_ERRATAS_DE_MARCAS_REGISTRADAS_GACETA_DE_JULIO_DEL_2016  # {DEBE_DECIR, DICE, ERROR_CORREGIDO}

# A MARCAS gaceta that is absent from GACETA_COLUMNAS (reachable .get default).
MISSING_GACETA = Gaceta.SIGNOS_DISTINTIVOS_CADUCOS

# Cross-area decoys.
PATENTES_GACETA = Gaceta.SOLICITUDES_DE_PATENTE_DE_REGISTROS_DE_MODELO_DE_UTILIDAD_Y_DE_DISENOS_INDUSTRIALES
PATENTES_SECCION = Seccion.SOLICITUDES_DE_PATENTE

# Datos (constructed once; Dato validates valor/fecha vs column kind on init).
DATO_CLASE = Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="42")
DATO_EXP_OR = Dato(operador=Operador.OR, columna=Columna.EXPEDIENTE, valor="ZESTO")
DATO_EXP_EMPTY = Dato(operador=Operador.EMPTY, columna=Columna.EXPEDIENTE, valor="ZESTO")
DATO_FECHA = Dato(operador=Operador.EMPTY, columna=Columna.FECHA_DE_PRESENTACION, fecha=date(2026, 1, 1))
DATO_TITULO = Dato(operador=Operador.EMPTY, columna=Columna.TITULO, valor="x")

TODAY = date.today()

SESSION = object()  # search() forwards the session untouched.


def run(coro):
    return asyncio.run(coro)


def validate(**over):
    """input_validation over a valid base, with field overrides."""
    kw = dict(area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE])
    kw.update(over)
    return input_validation(**kw)


def _patch_rwt(monkeypatch, status: int = 200, res=None) -> dict:
    captured: dict = {}

    async def fake_rwt(session, method, url, payload):
        captured.update(session=session, method=method, url=url, payload=payload)
        return status, ({"data": []} if res is None else res)

    monkeypatch.setattr(advanced_search, "request_with_token", fake_rwt)
    return captured


# ===========================================================================
# is_list_of for Dato / Seccion (should PASS)
# ===========================================================================
def test_is_list_of_dato() -> None:
    assert is_list_of([DATO_CLASE, DATO_EXP_OR], Dato) is True
    assert is_list_of([DATO_CLASE, "x"], Dato) is False


def test_is_list_of_seccion() -> None:
    assert is_list_of([SECCION], Seccion) is True
    assert is_list_of([SECCION, GACETA], Seccion) is False  # Gaceta is not Seccion


# ===========================================================================
# Dato: construction + to_payload (serialized by build_payload) — should PASS
# ===========================================================================
def test_dato_valor_requires_nonempty() -> None:
    with pytest.raises(ValueError):
        Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="")


def test_dato_valor_rejects_fecha() -> None:
    with pytest.raises(ValueError):
        Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="42", fecha=date(2026, 1, 1))


def test_dato_fecha_requires_fecha() -> None:
    with pytest.raises(ValueError):
        Dato(operador=Operador.EMPTY, columna=Columna.FECHA_DE_PRESENTACION)


def test_dato_fecha_rejects_valor() -> None:
    with pytest.raises(ValueError):
        Dato(
            operador=Operador.EMPTY,
            columna=Columna.FECHA_DE_PRESENTACION,
            valor="x",
            fecha=date(2026, 1, 1),
        )


def test_dato_valor_to_payload() -> None:
    assert DATO_CLASE.to_payload() == {
        "operador": "",
        "columna": "Clase",
        "valor": "42",
        "fecha": "",
    }


def test_dato_fecha_to_payload() -> None:
    p = DATO_FECHA.to_payload()
    assert p["valor"] == ""
    # fecha is pinned to midnight Mexico City (UTC-6) -> 06:00:00Z.
    assert p["fecha"] == "2026-01-01T06:00:00.000Z"


# ===========================================================================
# input_validation: valid cases (should PASS)
# ===========================================================================
def test_valid_full() -> None:
    ok, msg = validate()
    assert ok is True
    assert msg == "OK"


def test_valid_area_and_datos_only() -> None:
    # No gaceta and no seccion: column searchability is NOT checked at all.
    ok, _ = input_validation(area=AREA, gacetas=None, secciones=None, datos=[DATO_TITULO])
    assert ok is True


def test_valid_two_datos() -> None:
    ok, _ = validate(datos=[DATO_CLASE, DATO_EXP_OR])
    assert ok is True


def test_valid_fecha_column() -> None:
    ok, _ = validate(datos=[DATO_FECHA])
    assert ok is True


def test_valid_with_dates() -> None:
    ok, _ = validate(fecha_desde=date(2026, 1, 5), fecha_hasta=date(2026, 2, 9))
    assert ok is True


# ===========================================================================
# input_validation: coherence + datos rules (should PASS)
# ===========================================================================
def test_area_wrong_type_rejected() -> None:
    ok, msg = validate(area="x")
    assert ok is False
    assert "area" in msg


def test_gacetas_not_list_of_gaceta_rejected() -> None:
    ok, msg = validate(gacetas=[1])
    assert ok is False
    assert "gacetas" in msg


def test_secciones_without_gaceta_rejected() -> None:
    ok, msg = input_validation(area=AREA, gacetas=None, secciones=[SECCION], datos=[DATO_CLASE])
    assert ok is False
    assert "secciones require at least one gaceta" in msg


def test_gaceta_area_mismatch_rejected() -> None:
    ok, msg = validate(gacetas=[PATENTES_GACETA], secciones=None)
    assert ok is False
    assert "PATENTES" in msg


def test_seccion_area_mismatch_rejected() -> None:
    ok, msg = input_validation(
        area=AREA, gacetas=[GACETA], secciones=[PATENTES_SECCION], datos=[DATO_CLASE]
    )
    assert ok is False
    assert "seccion" in msg


def test_datos_empty_rejected() -> None:
    ok, msg = validate(datos=[])
    assert ok is False
    assert "at least one dato" in msg


def test_datos_none_rejected() -> None:
    ok, msg = validate(datos=None)
    assert ok is False
    assert "list of Dato" in msg


def test_more_than_two_datos_rejected() -> None:
    ok, msg = validate(datos=[DATO_CLASE, DATO_EXP_OR, DATO_EXP_OR])
    assert ok is False
    assert "at most two" in msg


def test_first_dato_must_have_empty_operador() -> None:
    ok, msg = validate(datos=[DATO_EXP_OR])
    assert ok is False
    assert "first dato" in msg


def test_second_dato_must_have_nonempty_operador() -> None:
    ok, msg = validate(datos=[DATO_CLASE, DATO_EXP_EMPTY])
    assert ok is False
    assert "second dato" in msg


def test_column_invalid_for_gaceta_rejected() -> None:
    ok, msg = validate(datos=[DATO_TITULO])  # TITULO not searchable in GACETA
    assert ok is False
    assert "not a valid column for gaceta" in msg


def test_column_valid_for_gaceta_but_invalid_for_seccion() -> None:
    # EXPEDIENTE passes the broad gaceta but fails the narrow seccion.
    ok, msg = input_validation(
        area=AREA,
        gacetas=[BROAD_GACETA],
        secciones=[NARROW_SECCION],
        datos=[DATO_EXP_EMPTY],
    )
    assert ok is False
    assert "not a valid column for seccion" in msg


def test_missing_gaceta_degrades_to_column_error() -> None:
    # A gaceta absent from GACETA_COLUMNAS gets .get(g, set()): every column is
    # then "invalid", so a valid-looking search fails with a column error
    # rather than something clearer like "gaceta not searchable".
    ok, msg = input_validation(
        area=AREA, gacetas=[MISSING_GACETA], secciones=None, datos=[DATO_CLASE]
    )
    assert ok is False
    assert "not a valid column for gaceta" in msg


# ===========================================================================
# build_payload: shape + quirks (should PASS)
# ===========================================================================
def test_build_payload_full_shape() -> None:
    payload = build_payload(
        area=AREA,
        gacetas=[GACETA, GACETA],  # duplicates are preserved
        secciones=[SECCION],
        datos=[DATO_CLASE, DATO_EXP_OR],
        fecha_desde=date(2026, 1, 5),
        fecha_hasta=date(2026, 2, 9),
        recaptcha=REAL_RECAPTCHA_TOKEN,
    )
    assert payload["idArea"] == str(AREA.value)
    assert payload["idGaceta"] == [GACETA.id_gaceta, GACETA.id_gaceta]
    assert payload["idSeccion"] == [SECCION.id_seccion]
    assert payload["datos"] == [DATO_CLASE.to_payload(), DATO_EXP_OR.to_payload()]
    # day-first dates, and note the Capitalised keys (Fecha*, not fecha*).
    assert payload["FechaDesde"] == "05-01-2026"
    assert payload["FechaHasta"] == "09-02-2026"
    assert payload["reCaptchaToken"] == REAL_RECAPTCHA_TOKEN


def test_build_payload_uses_capital_fecha_keys() -> None:
    # Quirk: advanced uses "FechaDesde"/"FechaHasta"; records/copies use
    # lowercase "fechaDesde"/"fechaHasta".
    payload = build_payload(area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE])
    assert "FechaDesde" in payload and "fechaDesde" not in payload


def test_build_payload_none_collections() -> None:
    payload = build_payload(area=AREA, gacetas=None, secciones=None, datos=[DATO_CLASE])
    assert payload["idGaceta"] == []
    assert payload["idSeccion"] == []


def test_build_payload_empty_dates() -> None:
    payload = build_payload(area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE])
    assert payload["FechaDesde"] == ""
    assert payload["FechaHasta"] == ""


def test_build_payload_invalid_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_payload(area=AREA, gacetas=None, secciones=None, datos=[])


# ===========================================================================
# search(): wiring (should PASS)
# ===========================================================================
def test_search_wires_request(monkeypatch) -> None:
    cap = _patch_rwt(monkeypatch, 200, {"data": [1, 2]})
    status, res = run(
        search(SESSION, area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE])
    )
    assert (status, res) == (200, {"data": [1, 2]})
    assert cap["session"] is SESSION
    assert cap["method"] == RequestMethods.POST
    assert cap["url"] == URL
    assert cap["payload"]["idArea"] == str(AREA.value)
    assert cap["payload"]["datos"] == [DATO_CLASE.to_payload()]


def test_search_returns_non_200_without_raising(monkeypatch) -> None:
    _patch_rwt(monkeypatch, 500, "boom")
    status, res = run(
        search(SESSION, area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE])
    )
    assert status == 500
    assert res == "boom"


def test_search_invalid_raises_before_request(monkeypatch) -> None:
    calls = {"n": 0}

    async def fake(**kwargs):
        calls["n"] += 1
        return 200, {}

    monkeypatch.setattr(advanced_search, "request_with_token", fake)
    with pytest.raises(ValueError):
        run(search(SESSION, area=AREA, gacetas=None, secciones=None, datos=[]))
    assert calls["n"] == 0


# ===========================================================================
# INTENDED behaviour — now enforced.
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
    ok, _ = validate(fecha_desde=desde, fecha_hasta=hasta)
    assert ok is True


def test_build_datetime_normalized_to_date() -> None:
    # A datetime is floored to its date; build_payload serializes the date part.
    payload = build_payload(
        area=AREA,
        gacetas=[GACETA],
        secciones=[SECCION],
        datos=[DATO_CLASE],
        fecha_desde=datetime(2020, 1, 1),  # type: ignore[arg-type]
        fecha_hasta=datetime(2020, 1, 1),  # type: ignore[arg-type]
    )
    assert payload["FechaDesde"] == payload["FechaHasta"] == "01-01-2020"


def test_seccion_missing_from_map_rejects_cleanly(monkeypatch) -> None:
    # Simulate the hand-maintained map drifting out of sync (a newly added
    # Seccion not yet listed). A missing Gaceta degrades gracefully via
    # .get(g, set()); a missing Seccion uses SECCION_COLUMNAS[s] and raises
    # KeyError. INTENDED: reject cleanly like the gaceta path.
    monkeypatch.delitem(advanced_search.SECCION_COLUMNAS, SECCION)
    ok, _ = input_validation(
        area=AREA, gacetas=[GACETA], secciones=[SECCION], datos=[DATO_CLASE]
    )
    assert ok is False

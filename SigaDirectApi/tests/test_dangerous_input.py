"""Weird & dangerous input / type checks for what this client actually SENDS to
the (unknown) SIGA ASP.NET / Kestrel backend.

Almost every field on the wire is constrained: idArea/idGaceta/idSeccion/columna
come from enums, busqueda is an int, dates are typed, recaptcha is regex-gated.
The ONE free-form, caller-controlled value that reaches the server is the search
term — Dato.valor (plus Dato.fecha for FECHA columns). It used to be validated for
*truthiness* only, never type or content, so a non-string, bytes, control chars,
null bytes or unbounded length all sailed through to a server whose parsing rules
we don't know.

Dato.valor is now a clean, bounded string (str, non-blank, <=512 chars, no
control chars) and Dato.fecha is floored to a pure date. These tests assert that
hardened contract; one documented gap (no recaptcha upper length) remains.

Pure/offline: nothing here touches the network.
"""

import pytest
from datetime import date, datetime, timedelta

import records_search
from constants import Columna, Dato, Operador, mexico_today

# A VALOR-kind column and a FECHA-kind column.
COL_VALOR = Columna.CLASE
COL_FECHA = Columna.FECHA_DE_PRESENTACION


def valor_dato(valor) -> Dato:
    return Dato(operador=Operador.EMPTY, columna=COL_VALOR, valor=valor)


# ===========================================================================
# Remaining documented gap (not yet hardened): recaptcha has no upper length.
# ===========================================================================
def test_recaptcha_has_no_upper_length_bound() -> None:
    # The regex is `{20,}` with no ceiling: a megabyte of valid charset is
    # accepted and would be posted verbatim. Real Google tokens are ~1.6 KB.
    ok, _ = records_search.input_validation(busqueda=3618676, recaptcha="A" * 1_000_000)
    assert ok is True


# ===========================================================================
# Hardening contract: Dato.valor is a clean, bounded string; Dato.fecha a pure date.
# ===========================================================================
@pytest.mark.parametrize("bad", [42, True, 3.14, ["a"], {"k": "v"}, b"x"])
def test_nonstr_valor_should_be_rejected(bad) -> None:
    with pytest.raises(ValueError):
        valor_dato(bad)  # type: ignore[arg-type]


def test_blank_valor_should_be_rejected() -> None:
    with pytest.raises(ValueError):
        valor_dato("   ")


def test_null_byte_valor_should_be_rejected() -> None:
    with pytest.raises(ValueError):
        valor_dato("a\x00b")


def test_control_char_valor_should_be_rejected() -> None:
    with pytest.raises(ValueError):
        valor_dato("a\x07b")  # bell


def test_oversized_valor_should_be_rejected() -> None:
    # Capped at VALOR_MAX_LEN (512); a search term can't be arbitrarily large.
    with pytest.raises(ValueError):
        valor_dato("A" * 100_000)


def test_datetime_fecha_normalized_to_date() -> None:
    # A datetime (subclass of date) is floored to a pure date: the time-of-day is
    # meaningless here (we hardcode T06:00:00Z), so it's accepted and normalized.
    dato = Dato(operador=Operador.EMPTY, columna=COL_FECHA, fecha=datetime(2026, 1, 1, 12, 30))
    assert dato.fecha == date(2026, 1, 1)
    assert dato.to_payload()["fecha"] == "2026-01-01T06:00:00.000Z"


def test_future_fecha_should_be_rejected() -> None:
    # A FECHA term can't search a date that hasn't arrived in Mexico yet.
    future = mexico_today() + timedelta(days=1)
    with pytest.raises(ValueError):
        Dato(operador=Operador.EMPTY, columna=COL_FECHA, fecha=future)

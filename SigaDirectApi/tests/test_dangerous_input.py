"""Weird & dangerous input / type checks for what this client actually SENDS to
the (unknown) SIGA ASP.NET / Kestrel backend.

Almost every field on the wire is constrained: idArea/idGaceta/idSeccion/columna
come from enums, busqueda is an int, dates are typed, recaptcha is regex-gated.
The ONE free-form, caller-controlled value that reaches the server is the search
term — Dato.valor (plus Dato.fecha for FECHA columns). And Dato validates only
the *truthiness* of valor, never its type or content. So a non-string sails
through and is serialized as the wrong JSON type, a bytes value crashes the whole
request at orjson.dumps, and control characters / null bytes / unbounded length
pass straight through to a server whose parsing rules we don't know.

Section A documents the current (dangerous) behaviour and PASSES — proof the
holes are real. Section B asserts the INTENDED hardening (Dato.valor must be a
clean, bounded string; Dato.fecha must be a pure date) and currently FAILS.

Pure/offline: nothing here touches the network.
"""

import orjson
import pytest
from datetime import date, datetime

import records_search
from constants import Columna, Dato, Operador

# A VALOR-kind column and a FECHA-kind column.
COL_VALOR = Columna.CLASE
COL_FECHA = Columna.FECHA_DE_PRESENTACION


def valor_dato(valor) -> Dato:
    return Dato(operador=Operador.EMPTY, columna=COL_VALOR, valor=valor)


# ===========================================================================
# Section A — current behaviour, PASSES. The danger is real and on-the-wire.
# ===========================================================================
def test_int_valor_serializes_as_json_number() -> None:
    # 42 is truthy, so Dato accepts it; it then ships as a JSON *number*, not a
    # string. An ASP.NET model expecting `string valor` may 400 or coerce.
    wire = orjson.dumps(valor_dato(42).to_payload())
    assert b'"valor":42' in wire


def test_bool_valor_serializes_as_json_bool() -> None:
    assert b'"valor":true' in orjson.dumps(valor_dato(True).to_payload())


def test_list_valor_serializes_as_json_array() -> None:
    assert b'"valor":["a"]' in orjson.dumps(valor_dato(["a"]).to_payload())


def test_bytes_valor_crashes_request_serialization() -> None:
    # bytes is truthy -> Dato accepts it -> but orjson.dumps (what the request
    # layer runs on the whole payload) raises. One poisoned term means NO search
    # runs at all, not just a bad field.
    dato = valor_dato(b"x")  # constructs fine
    with pytest.raises(TypeError):
        orjson.dumps(dato.to_payload())


def test_falsy_nonstr_valor_rejected_inconsistently() -> None:
    # 42 is accepted but 0 is rejected — purely because of truthiness, not type.
    assert valor_dato(42).to_payload()["valor"] == 42
    with pytest.raises(ValueError):
        valor_dato(0)


def test_control_and_null_chars_pass_through() -> None:
    nasty = "a\x00\r\n\x07b"  # null byte, CR/LF, bell
    assert valor_dato(nasty).to_payload()["valor"] == nasty


def test_whitespace_only_valor_accepted() -> None:
    # A blank search term (spaces/tabs) is accepted as a "non-empty" valor.
    assert valor_dato("  \t ").to_payload()["valor"] == "  \t "


def test_datetime_fecha_makes_malformed_timestamp() -> None:
    # FECHA column: Dato checks `fecha is None` but not that it is a *pure* date.
    # A datetime's isoformat() already has a 'T', so the appended time yields a
    # malformed double-'T' timestamp.
    dato = Dato(operador=Operador.EMPTY, columna=COL_FECHA, fecha=datetime(2026, 1, 1, 12, 30))
    assert dato.to_payload()["fecha"] == "2026-01-01T12:30:00T06:00:00.000Z"


def test_recaptcha_has_no_upper_length_bound() -> None:
    # The regex is `{20,}` with no ceiling: a megabyte of valid charset is
    # accepted and would be posted verbatim. Real Google tokens are ~1.6 KB.
    ok, _ = records_search.input_validation(busqueda=3618676, recaptcha="A" * 1_000_000)
    assert ok is True


# ===========================================================================
# Section B — INTENDED hardening. Currently FAILS / ERRORS.
# Dato.valor should be a clean, bounded string; Dato.fecha a pure date.
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
    # No length cap today: a search term can be arbitrarily large.
    with pytest.raises(ValueError):
        valor_dato("A" * 100_000)


def test_datetime_fecha_should_be_rejected() -> None:
    # A datetime (subclass of date) must not pass as a FECHA value.
    with pytest.raises(ValueError):
        Dato(operador=Operador.EMPTY, columna=COL_FECHA, fecha=datetime(2026, 1, 1, 12, 30))

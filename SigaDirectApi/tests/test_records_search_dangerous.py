"""Weird & dangerous input / type checks for records_search specifically,
against the unknown SIGA ASP.NET / Kestrel backend.

records_search has no Dato/valor; its one caller-controlled scalar is `busqueda`,
which is type-checked (int, not bool, >= 10) but has NO upper bound. Python ints
are unbounded, so a value past Int32 / Int64 range is accepted and stringified
onto the wire — where an ASP.NET model bound to `int`/`long` (or a SQL int/bigint
column) will overflow, throw, or silently mis-store it. The other unbounded
surface is `gacetas`: a 5000-element list is accepted even though only 26 distinct
gacetas exist, so any such list is mostly duplicates and an oversized payload.

Section A documents the current (dangerous) behaviour and PASSES. Section B
asserts the INTENDED hardening (busqueda bounded to a server-representable range;
gacetas no longer than the universe of gacetas) and currently FAILS.

Pure/offline: nothing here touches the network.
"""

import pytest

from constants import Area, Gaceta
from records_search import build_payload, input_validation

INT32_MAX = 2**31 - 1  # SQL Server 'int' / .NET Int32
INT64_MAX = 2**63 - 1  # SQL Server 'bigint' / .NET Int64

AREA = Area.MARCAS
G = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO


# ===========================================================================
# Section A — current behaviour, PASSES. The danger is real and on-the-wire.
# ===========================================================================
def test_busqueda_above_int32_accepted() -> None:
    # If the backend column/param is a 32-bit int (the SQL Server default), this
    # is already out of range, yet validation waves it through.
    ok, _ = input_validation(busqueda=INT32_MAX + 1)
    assert ok is True
    assert build_payload(busqueda=INT32_MAX + 1)["busqueda"] == "2147483648"


def test_busqueda_above_int64_accepted() -> None:
    ok, _ = input_validation(busqueda=INT64_MAX + 1)
    assert ok is True


def test_busqueda_absurd_magnitude_reaches_wire() -> None:
    n = 10**40  # 41 digits; no fixed-width integer can represent it
    assert input_validation(busqueda=n)[0] is True
    wire = build_payload(busqueda=n)["busqueda"]
    assert wire == "1" + "0" * 40
    assert len(wire) == 41


def test_gacetas_list_unbounded_with_dupes_kept() -> None:
    # Only 26 Gaceta members exist, yet a 5000-element all-duplicate list is
    # accepted and shipped verbatim as idGaceta.
    big = [G] * 5000
    assert input_validation(busqueda=10, area=AREA, gacetas=big)[0] is True
    assert build_payload(busqueda=10, area=AREA, gacetas=big)["idGaceta"] == [G.id_gaceta] * 5000


# ===========================================================================
# Section B — INTENDED hardening. Currently FAILS.
# ===========================================================================
def test_busqueda_beyond_int64_should_be_rejected() -> None:
    # 2**63 exceeds signed Int64; no fixed-width server integer holds it, so it
    # must be rejected before it reaches the ASP.NET/SQL backend.
    ok, _ = input_validation(busqueda=2**63)
    assert ok is False


def test_busqueda_absurd_magnitude_should_be_rejected() -> None:
    ok, _ = input_validation(busqueda=10**40)
    assert ok is False


def test_gacetas_longer_than_universe_should_be_rejected() -> None:
    # There are only len(Gaceta) distinct gacetas; a longer list is necessarily
    # duplicates — malformed, and an unbounded payload to an unknown server.
    big = [G] * (len(list(Gaceta)) + 1)
    ok, _ = input_validation(busqueda=10, area=AREA, gacetas=big)
    assert ok is False

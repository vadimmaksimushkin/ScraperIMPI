"""Weird & dangerous input / type checks for records_search specifically,
against the unknown SIGA ASP.NET / Kestrel backend.

records_search has no Dato/valor; its one caller-controlled scalar is `busqueda`,
which is type-checked (int, not bool, >= 10) but has NO upper bound. Python ints
are unbounded, so a value past Int32 / Int64 range is accepted and stringified
onto the wire — where an ASP.NET model bound to `int`/`long` (or a SQL int/bigint
column) will overflow, throw, or silently mis-store it. The other unbounded
surface is `gacetas`: a 5000-element list is accepted even though only 26 distinct
gacetas exist, so any such list is mostly duplicates and an oversized payload.

The hardening is now enforced: busqueda is bounded to a server-representable
range (Int64), and gacetas may be no longer than the universe of gacetas.

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
# Still accepted: we cap at Int64, NOT Int32 — an Int32-overflowing value is
# deliberately let through (the backend's true width is unknown).
# ===========================================================================
def test_busqueda_above_int32_accepted() -> None:
    ok, _ = input_validation(busqueda=INT32_MAX + 1)
    assert ok is True
    assert build_payload(busqueda=INT32_MAX + 1)["busqueda"] == "2147483648"


# ===========================================================================
# Section B — INTENDED hardening. Now enforced.
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

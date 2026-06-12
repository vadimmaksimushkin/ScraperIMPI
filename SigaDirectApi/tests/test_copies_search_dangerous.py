"""Weird & dangerous input / type checks for copies_search specifically,
against the unknown SIGA ASP.NET / Kestrel backend.

copies_search is the most locked-down module: area and gaceta are enums, dates
are typed, recaptcha is regex-gated — there is NO free-form scalar (no valor, no
busqueda). So the residual dangerous-input surface is narrow:

  1. The by-fecha endpoint queries a date RANGE, and fecha_desde has no lower
     floor. A caller can ask for `date(1, 1, 1) .. today` — ~2000 years — which
     is nonsensical (no gaceta predates SIGA) and an unbounded query to a server
     we don't control. base_search even defines DEFAULT_YEARS_BACK = 20 as the
     intended floor, but copies never applies it.
  2. The "DOES NOT EXIST" sentinel areas (AREA_7/AREA_8) are valid Area members,
     so a bogus idArea reaches the wire on the by-fecha endpoint.
  3. recaptcha has no upper length bound (shared with the other modules).

Section A documents the current (dangerous) behaviour and PASSES. Section B
asserts the INTENDED hardening (a sane lower floor on fecha_desde) and FAILS.

Pure/offline: nothing here touches the network.
"""

from datetime import date

import pytest

from base_search import DEFAULT_YEARS_BACK
from constants import Area
from copies_search import build_url_payload, input_validation

AREA = Area.MARCAS
TODAY = date.today()
# A date one year older than the design floor (today - DEFAULT_YEARS_BACK).
BEFORE_FLOOR = date(TODAY.year - DEFAULT_YEARS_BACK - 1, 1, 1)


# ===========================================================================
# Section A — current behaviour, PASSES. The danger is real and on-the-wire.
# ===========================================================================
def test_ancient_date_range_accepted() -> None:
    # Year 1 .. today: ~2000-year span, no gaceta predates SIGA, yet it validates
    # and ships a well-formed (but absurd) ISO date.
    ok, _ = input_validation(area=AREA, fecha_desde=date(1, 1, 1), fecha_hasta=TODAY)
    assert ok is True
    _, payload = build_url_payload(area=AREA, fecha_desde=date(1, 1, 1), fecha_hasta=TODAY)
    assert payload["fechaDesde"] == "0001-01-01"


def test_range_far_older_than_design_floor_accepted() -> None:
    # base_search.DEFAULT_YEARS_BACK == 20, yet a date well before that floor is
    # accepted with no clamping.
    assert input_validation(area=AREA, fecha_desde=BEFORE_FLOOR, fecha_hasta=TODAY)[0] is True


def test_nonexistent_area_by_fecha_reaches_wire() -> None:
    # AREA_7 is flagged "DOES NOT EXIST" in constants, but it's a real Area
    # member, so its code is sent verbatim on the by-fecha endpoint.
    ok, _ = input_validation(area=Area.AREA_7, fecha_desde=date(2026, 1, 1), fecha_hasta=TODAY)
    assert ok is True
    _, payload = build_url_payload(area=Area.AREA_7, fecha_desde=date(2026, 1, 1), fecha_hasta=TODAY)
    assert payload["idArea"] == str(Area.AREA_7.value)  # "7"


def test_recaptcha_has_no_upper_length_bound() -> None:
    ok, _ = input_validation(
        area=AREA, fecha_desde=date(2026, 1, 1), fecha_hasta=TODAY, recaptcha="A" * 1_000_000
    )
    assert ok is True


# ===========================================================================
# Section B — INTENDED hardening. Currently FAILS.
# ===========================================================================
def test_prehistoric_date_should_be_rejected() -> None:
    # An obviously impossible search window (year 1) must not produce an
    # unbounded query against the backend.
    ok, _ = input_validation(area=AREA, fecha_desde=date(1, 1, 1), fecha_hasta=TODAY)
    assert ok is False


def test_date_before_design_floor_should_be_rejected() -> None:
    # The design pins the lower bound at today - DEFAULT_YEARS_BACK (20y). A
    # fecha_desde older than that should be rejected (or clamped), not silently
    # turned into an unbounded query.
    ok, _ = input_validation(area=AREA, fecha_desde=BEFORE_FLOOR, fecha_hasta=TODAY)
    assert ok is False

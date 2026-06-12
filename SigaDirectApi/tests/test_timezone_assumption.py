"""Timezone assumption: the SIGA domain is Mexico City (UTC-6) — Dato.to_payload
even hardcodes T06:00:00Z — but every validator judges "future" with the RUNNER's
local `date.today()`, not Mexico's. If the runner sits 1-2 hours off UTC-6, there
is a near-midnight window where the runner's calendar date differs from Mexico's,
so `fecha_hasta > date.today()` is wrong in BOTH directions:

  * runner EAST of Mexico (e.g. UTC-4/-5): its date rolls over first, so a date
    that is still tomorrow (future) in Mexico is wrongly ACCEPTED.
  * runner WEST of Mexico (e.g. UTC-7/-8): its date lags, so a date that is
    already today (valid) in Mexico is wrongly REJECTED as future.

These tests simulate the runner's local date deterministically (no real clock):
the runner date is computed from a fixed UTC instant + offset, and the validator's
`date.today()` is patched to return it. The metaclass keeps `isinstance(x, date)`
working for real date args while overriding `today()`.

The "runner in Mexico" sanity test PASSES (proves the harness is sound). The
east/west tests assert the Mexico-correct outcome and currently FAIL — the fix is
to compare against America/Mexico_City's current date, not the host's.

Pure/offline.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import advanced_search
import copies_search
import records_search
from constants import Area, Columna, Dato, Operador

MEXICO = timezone(timedelta(hours=-6))

# A valid single Dato so advanced_search's date checks are the only gate.
_DATO = Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="42")


def _call_copies(fd: date, fh: date):
    return copies_search.input_validation(area=Area.MARCAS, fecha_desde=fd, fecha_hasta=fh)


def _call_records(fd: date, fh: date):
    return records_search.input_validation(busqueda=3618676, fecha_desde=fd, fecha_hasta=fh)


def _call_advanced(fd: date, fh: date):
    return advanced_search.input_validation(
        area=Area.MARCAS, gacetas=None, secciones=None, datos=[_DATO], fecha_desde=fd, fecha_hasta=fh
    )


MODULES = [
    (copies_search, _call_copies),
    (records_search, _call_records),
    (advanced_search, _call_advanced),
]
MODULE_IDS = ["copies", "records", "advanced"]


def _runner_date(instant_utc: datetime, offset_hours: int) -> date:
    return instant_utc.astimezone(timezone(timedelta(hours=offset_hours))).date()


def _mexico_date(instant_utc: datetime) -> date:
    return instant_utc.astimezone(MEXICO).date()


def _patch_today(monkeypatch, module, today_value: date) -> None:
    """Make module.date.today() return today_value while keeping isinstance()
    working for real date arguments."""

    class _DateMeta(type):
        def __instancecheck__(cls, obj):
            return isinstance(obj, date)

    class _FakeDate(date, metaclass=_DateMeta):
        @classmethod
        def today(cls):
            return today_value

    monkeypatch.setattr(module, "date", _FakeDate)


# A UTC instant that puts Mexico at 23:30 (just before its midnight): a runner
# 1-2h EAST has already rolled to the next day.
EAST_INSTANT = datetime(2026, 6, 12, 5, 30, tzinfo=timezone.utc)  # Mexico 2026-06-11 23:30
# A UTC instant that puts Mexico at 00:30 (just after its midnight): a runner
# 1-2h WEST is still on the previous day.
WEST_INSTANT = datetime(2026, 6, 12, 6, 30, tzinfo=timezone.utc)  # Mexico 2026-06-12 00:30


# ===========================================================================
# Sanity: a runner actually in Mexico (UTC-6) judges the boundary correctly.
# PASSES — proves the harness is sound and the bug is specifically the offset.
# ===========================================================================
@pytest.mark.parametrize("module, call", MODULES, ids=MODULE_IDS)
def test_runner_in_mexico_judges_boundary_correctly(monkeypatch, module, call) -> None:
    mexico_today = _mexico_date(WEST_INSTANT)
    runner_today = _runner_date(WEST_INSTANT, -6)
    assert runner_today == mexico_today
    _patch_today(monkeypatch, module, runner_today)

    ok_today, _ = call(mexico_today, mexico_today)
    ok_future, _ = call(mexico_today + timedelta(days=1), mexico_today + timedelta(days=1))
    assert ok_today is True
    assert ok_future is False


# ===========================================================================
# INTENDED behaviour — currently FAILS.
# ===========================================================================
@pytest.mark.parametrize("offset", [-4, -5], ids=["UTC-4", "UTC-5"])
@pytest.mark.parametrize("module, call", MODULES, ids=MODULE_IDS)
def test_runner_east_wrongly_accepts_mexico_future(monkeypatch, module, call, offset) -> None:
    # Runner is a calendar day ahead of Mexico; Mexico's "tomorrow" is future
    # there (no gaceta exists yet) but the runner accepts it.
    runner_today = _runner_date(EAST_INSTANT, offset)
    mexico_today = _mexico_date(EAST_INSTANT)
    assert runner_today > mexico_today  # discrepancy actually present
    mexico_future = mexico_today + timedelta(days=1)

    _patch_today(monkeypatch, module, runner_today)
    ok, _ = call(mexico_future, mexico_future)
    assert ok is False  # INTENDED: future-in-Mexico must be rejected


@pytest.mark.parametrize("offset", [-7, -8], ids=["UTC-7", "UTC-8"])
@pytest.mark.parametrize("module, call", MODULES, ids=MODULE_IDS)
def test_runner_west_wrongly_rejects_mexico_today(monkeypatch, module, call, offset) -> None:
    # Runner is a calendar day behind Mexico; Mexico's "today" is valid there
    # but the runner rejects it as future.
    runner_today = _runner_date(WEST_INSTANT, offset)
    mexico_today = _mexico_date(WEST_INSTANT)
    assert runner_today < mexico_today  # discrepancy actually present

    _patch_today(monkeypatch, module, runner_today)
    ok, _ = call(mexico_today, mexico_today)
    assert ok is True  # INTENDED: today-in-Mexico must be accepted

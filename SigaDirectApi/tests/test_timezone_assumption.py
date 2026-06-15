"""Timezone contract: the SIGA domain is Mexico City (UTC-6, no DST since 2022) —
the payloads even hardcode T06:00:00Z. "Future" must therefore be judged against
Mexico's current date, not the runner's host clock. constants.mexico_today()
centralizes that, and every date-range validator gates fecha_hasta on it.

These tests are deterministic (no real clock, no host-TZ dependence):
  * mexico_today() is exercised by freezing the UTC instant (patching
    constants.datetime), proving it tracks Mexico's calendar date, not UTC's.
  * each validator is exercised by patching its mexico_today reference to a fixed
    Mexico date, proving the future gate is judged there.

Pure/offline.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

import advanced_search
import constants
import copies_search
import records_search
from constants import Area, Columna, Dato, Operador, mexico_today

# A valid single Dato so advanced_search's date checks are the only gate.
_DATO = Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="42")


def _call_records(fd: date, fh: date):
    return records_search.input_validation(busqueda=3618676, fecha_desde=fd, fecha_hasta=fh)


def _call_copies(fd: date, fh: date):
    return copies_search.input_validation(area=Area.MARCAS, fecha_desde=fd, fecha_hasta=fh)


def _call_advanced(fd: date, fh: date):
    return advanced_search.input_validation(
        area=Area.MARCAS, gacetas=None, secciones=None, datos=[_DATO], fecha_desde=fd, fecha_hasta=fh
    )


MODULES = [
    (records_search, _call_records),
    (copies_search, _call_copies),
    (advanced_search, _call_advanced),
]
MODULE_IDS = ["records", "copies", "advanced"]


# ===========================================================================
# mexico_today(): tracks Mexico's calendar date from the absolute instant,
# independent of UTC's date and of the host timezone.
# ===========================================================================
def _freeze_instant(monkeypatch, instant_utc: datetime) -> None:
    """Freeze constants.datetime.now(tz) at a fixed UTC instant."""

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant_utc.astimezone(tz) if tz is not None else instant_utc.replace(tzinfo=None)

    monkeypatch.setattr(constants, "datetime", _FrozenDatetime)


@pytest.mark.parametrize(
    "instant_utc, expected",
    [
        # 23:30 in Mexico (UTC 05:30 the next day): UTC has rolled over, Mexico hasn't.
        (datetime(2026, 6, 12, 5, 30, tzinfo=timezone.utc), date(2026, 6, 11)),
        # 00:30 in Mexico (UTC 06:30): Mexico has just rolled to the new day.
        (datetime(2026, 6, 12, 6, 30, tzinfo=timezone.utc), date(2026, 6, 12)),
    ],
    ids=["just-before-mexico-midnight", "just-after-mexico-midnight"],
)
def test_mexico_today_follows_mexico_not_utc(monkeypatch, instant_utc, expected) -> None:
    _freeze_instant(monkeypatch, instant_utc)
    assert mexico_today() == expected


# ===========================================================================
# Every validator gates "future" on mexico_today(), not the host's date.
# ===========================================================================
MEXICO_NOW = date(2026, 6, 11)


@pytest.mark.parametrize("module, call", MODULES, ids=MODULE_IDS)
def test_future_in_mexico_is_rejected(monkeypatch, module, call) -> None:
    monkeypatch.setattr(module, "mexico_today", lambda: MEXICO_NOW)
    tomorrow = MEXICO_NOW + timedelta(days=1)
    ok, _ = call(tomorrow, tomorrow)
    assert ok is False


@pytest.mark.parametrize("module, call", MODULES, ids=MODULE_IDS)
def test_today_in_mexico_is_accepted(monkeypatch, module, call) -> None:
    monkeypatch.setattr(module, "mexico_today", lambda: MEXICO_NOW)
    ok, _ = call(MEXICO_NOW, MEXICO_NOW)
    assert ok is True

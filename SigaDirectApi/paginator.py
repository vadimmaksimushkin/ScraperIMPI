"""Defeat the 15000-row cap by walking the date window downward.

The SIGA fichas endpoints (advanced `GetSearchEstructurada`, records `GetFichas`)
return at most CAP=15000 rows, sorted by `fechaPuestaCirculacion` DESC, with no
pagination parameter. We page by narrowing the *upper* date bound: each capped
response's OLDEST day becomes the next request's `fecha_hasta`. Two consecutive
windows therefore overlap on exactly that one boundary day (the cap truncates it),
so we deduplicate by id-set membership.

Why id-set (and not a positional/anchor shortcut): proven against the live API on
2026-06-22, a page's partial boundary day is a SUBSET but NOT a PREFIX of the same
day fetched in full by the next window (the per-day order is a query-execution
artifact; the previously-cut rows come back *interleaved* among the ones we kept).
So only "drop the already-seen ids, keep everything else" is safe -- this is
`merge_two_lists.merge_v2`, generalized here over an adapter's id/date accessors.

Constraints learned from the API:
  * every endpoint rejects a lone `fecha_hasta` ("Both dates must be present or
    absent"), so we always send a (floor, hasta) window; floor defaults to 20y back.
  * copies (`GetEjemplares` / `GetEjemplaresArrayByFecha`) never approach the cap
    (a few thousand ejemplares at most), so for them the walk just returns the one
    response. `GetEjemplaresArrayByFecha` returns a dict-of-lists, which the copies
    adapter flattens.

Entry points:
  search_all   -> drain everything (optionally bounded by max_pages), one envelope.
  search_page  -> one window + an opaque cursor for stateless continuation.
"""
import asyncio
import base64
import hashlib
import logging
import struct
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable

import aiohttp
import orjson

from constants import (
    Area, Gaceta, Seccion, Dato, mexico_today,
)
import advanced_search
import copies_search
import records_search

log = logging.getLogger("siga.paginator")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

CAP = 15000              # server hard cap; len(raw) >= CAP means it was truncated
FLOOR_YEARS_BACK = 20    # default lower bound; older than any real SIGA data


def _floor() -> date:
    # matches copies_search's own >20y rejection boundary, so it never trips it.
    return mexico_today() - timedelta(days=365 * FLOOR_YEARS_BACK)


def _dmy(s: str) -> date:
    """'10/06/2026' (fichaPuestaCirculacion) -> date."""
    return datetime.strptime(s, "%d/%m/%Y").date()


# --------------------------------------------------------------------------- #
# Cursor: an opaque, self-verifying continuation token.
# Ported from merge_two_lists.py (hash_cursor/assemble_cursor/cursor_verification),
# with ids widened to uint64 so any int64 fichaId fits. Layout, base64url'd:
#     [ checksum(16) | seen ids (uint64 each) | stop_date ordinal (uint32) ]
# The checksum binds the token to its search params + seen + date, so a token from
# another search (or a tampered one) fails verification. base64 != encryption: this
# is a non-adversarial integrity/binding check, not a signature.
# --------------------------------------------------------------------------- #
_CHECKSUM_LEN = 16
_DATE_LEN = 4
_ID_SIZE = 8  # uint64 big-endian per seen id


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _checksum(params: dict[str, Any], seen: list[int], stop_date: date) -> bytes:
    # OPT_SORT_KEYS canonicalizes nested dict key order; orjson renders `date` ISO.
    payload = orjson.dumps(
        {"params": params, "seen": seen, "stop_date": stop_date},
        option=orjson.OPT_SORT_KEYS,
    )
    return hashlib.sha256(payload).digest()[:_CHECKSUM_LEN]


def encode_cursor(params: dict[str, Any], seen: list[int], stop_date: date) -> str:
    seen_bytes = b"".join(struct.pack(">Q", i) for i in seen)
    buf = _checksum(params, seen, stop_date) + seen_bytes + struct.pack(">I", stop_date.toordinal())
    return base64.urlsafe_b64encode(buf).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[bytes, list[int], date]:
    """(checksum, seen, stop_date); raises ValueError on a malformed token."""
    try:
        raw = _b64url_decode(cursor)
        seen_len = len(raw) - _CHECKSUM_LEN - _DATE_LEN
        if seen_len < 0 or seen_len % _ID_SIZE != 0:
            raise ValueError("bad length")
        check = raw[:_CHECKSUM_LEN]
        seen = list(struct.unpack(f">{seen_len // _ID_SIZE}Q", raw[_CHECKSUM_LEN:-_DATE_LEN]))
        stop_date = date.fromordinal(int.from_bytes(raw[-_DATE_LEN:], "big"))
    except (ValueError, struct.error, OverflowError) as exc:
        raise ValueError(f"malformed cursor: {exc}")
    return check, seen, stop_date


def verify_cursor(params: dict[str, Any], cursor: str) -> tuple[list[int], date]:
    """(seen, stop_date) if the cursor is well-formed AND belongs to `params`;
    raises ValueError otherwise."""
    check, seen, stop_date = decode_cursor(cursor)
    if check != _checksum(params, seen, stop_date):
        raise ValueError("cursor does not match this search")
    return seen, stop_date


# --------------------------------------------------------------------------- #
# Dedup (merge_v2, generalized) + boundary helper
# --------------------------------------------------------------------------- #
def merge_v2(
    results: list[dict[str, Any]],
    new_results: list[dict[str, Any]],
    get_id: Callable[[dict[str, Any]], int],
    get_date: Callable[[dict[str, Any]], date],
) -> list[dict[str, Any]]:
    """In-place merge of two DESC-by-date lists that overlap only on `results`'
    oldest day. Scans just that boundary block (never the bulk) to dedup by id,
    then extends with everything past it. Order-independent within the day."""
    if not new_results:
        return results
    if not results:
        results.extend(new_results)
        return results
    overlap_date = get_date(results[-1])
    seen: set[int] = set()
    k = len(results) - 1
    while k >= 0 and get_date(results[k]) == overlap_date:
        seen.add(get_id(results[k]))
        k -= 1
    i, n = 0, len(new_results)
    while i < n and get_date(new_results[i]) == overlap_date:
        if get_id(new_results[i]) not in seen:
            results.append(new_results[i])
        i += 1
    results.extend(new_results[i:])
    return results


def _boundary(adapter: "Adapter", records: list[dict[str, Any]]) -> tuple[date, list[int]]:
    """Oldest date in `records` and every id on it -- the dedup anchor the next
    window needs (computed by value, so it never relies on sort stability)."""
    oldest = min(adapter.get_date(r) for r in records)
    ids = [adapter.get_id(r) for r in records if adapter.get_date(r) == oldest]
    return oldest, ids


# --------------------------------------------------------------------------- #
# Adapter: the only per-endpoint knowledge the engine needs.
# --------------------------------------------------------------------------- #
@dataclass
class Adapter:
    search: Callable[[aiohttp.ClientSession, date, date], Awaitable[tuple[int, Any]]]
    extract: Callable[[Any], list[dict[str, Any]]]   # response -> flat record list
    get_id: Callable[[dict[str, Any]], int]
    get_date: Callable[[dict[str, Any]], date]
    params: dict[str, Any]                            # canonical search id, for the cursor checksum


@dataclass
class Page:
    records: list[dict[str, Any]]
    cursor: str | None        # None => no more pages
    status: int


def _fichas_data(res: Any) -> list[dict[str, Any]]:
    return res.get("data", []) if isinstance(res, dict) else []


def advanced_adapter(area: Area, gacetas: list[Gaceta], secciones: list[Seccion],
                     datos: list[Dato]) -> Adapter:
    async def _search(session, desde, hasta):
        return await advanced_search.search(
            session, area=area, gacetas=gacetas, secciones=secciones, datos=datos,
            fecha_desde=desde, fecha_hasta=hasta)
    return Adapter(
        search=_search,
        extract=_fichas_data,
        get_id=lambda r: r["fichaId"],
        get_date=lambda r: _dmy(r["fechaPuestaCirculacion"]),
        params={
            "kind": "advanced", "area": area.value,
            "gacetas": sorted(g.id_gaceta for g in gacetas),
            "secciones": sorted(s.id_seccion for s in secciones),
            "datos": [d.to_payload() for d in datos],
        },
    )


def records_adapter(busqueda: int, area: Area | None = None,
                    gacetas: list[Gaceta] | None = None) -> Adapter:
    async def _search(session, desde, hasta):
        return await records_search.search(
            session, busqueda=busqueda, area=area, gacetas=gacetas,
            fecha_desde=desde, fecha_hasta=hasta)
    return Adapter(
        search=_search,
        extract=_fichas_data,
        get_id=lambda r: r["fichaId"],
        get_date=lambda r: _dmy(r["fechaPuestaCirculacion"]),
        params={
            "kind": "records", "busqueda": busqueda,
            "area": area.value if area else None,
            "gacetas": sorted(g.id_gaceta for g in gacetas) if gacetas else [],
        },
    )


def _copies_data(res: Any) -> list[dict[str, Any]]:
    # GetEjemplares -> data is a list; GetEjemplaresArrayByFecha -> dict of lists.
    if not isinstance(res, dict):
        return []
    data = res.get("data")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        out: list[dict[str, Any]] = []
        for v in data.values():
            if isinstance(v, list):
                out.extend(v)
        return out
    return []


def copies_adapter(area: Area, gaceta: Gaceta | None = None) -> Adapter:
    async def _search(session, desde, hasta):
        return await copies_search.search(
            session, area=area, gaceta=gaceta, fecha_desde=desde, fecha_hasta=hasta)
    return Adapter(
        search=_search,
        extract=_copies_data,
        get_id=lambda r: r["i_id"],
        get_date=lambda r: date(r["i_anio"], r["i_mes"], r["i_dia"]),
        params={
            "kind": "copies", "area": area.value,
            "gaceta": gaceta.id_gaceta if gaceta else None,
        },
    )


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #
def _envelope(first_res: Any, data: list[dict[str, Any]], requests: int,
              cursor: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {}
    if isinstance(first_res, dict):
        for k in ("successed", "message", "errors"):
            if k in first_res:
                base[k] = first_res[k]
    base["data"] = data
    base["pagination"] = {
        "requests": requests,
        "returned": len(data),
        "truncated": cursor is not None,
        "cursor": cursor,
    }
    return base


async def search_all(
    session: aiohttp.ClientSession,
    adapter: Adapter,
    *,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Walk windows downward, dedup with merge_v2, return one envelope whose
    `data` is the full (un-capped) deduped list. If `max_pages` cuts the walk
    short while more remains, `pagination.cursor` is a token to resume from."""
    floor = fecha_desde or _floor()
    hasta = fecha_hasta or mexico_today()
    results: list[dict[str, Any]] = []
    first_res: Any = None
    requests = 0
    cursor: str | None = None

    while True:
        _, res = await adapter.search(session, floor, hasta)
        requests += 1
        if first_res is None:
            first_res = res
        raw = adapter.extract(res)
        merge_v2(results, raw, adapter.get_id, adapter.get_date)
        if len(raw) < CAP:                     # window fully drained -> done
            break
        oldest, _ = _boundary(adapter, raw)
        if max_pages is not None and requests >= max_pages:
            b_date, b_ids = _boundary(adapter, results)   # accumulator's boundary day
            cursor = encode_cursor(adapter.params, b_ids, b_date)
            break
        hasta = oldest                         # next window; overlap on `oldest`

    return _envelope(first_res, results, requests, cursor)


async def search_page(
    session: aiohttp.ClientSession,
    adapter: Adapter,
    *,
    cursor: str | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
) -> Page:
    """One window, stateless. Without a cursor: newest CAP rows. With a cursor:
    resume at its `stop_date`, dropping the boundary-day ids it carries. Returns
    the (deduped) records plus the next cursor, or None when fully drained."""
    floor = fecha_desde or _floor()
    if cursor is None:
        hasta = fecha_hasta or mexico_today()
        seen_set: frozenset[int] = frozenset()
    else:
        seen, stop_date = verify_cursor(adapter.params, cursor)
        hasta, seen_set = stop_date, frozenset(seen)

    _, res = await adapter.search(session, floor, hasta)
    raw = adapter.extract(res)
    records = [r for r in raw if adapter.get_id(r) not in seen_set] if seen_set else list(raw)

    if len(raw) < CAP:
        return Page(records=records, cursor=None, status=200)
    b_date, b_ids = _boundary(adapter, raw)    # this window's oldest day, fully delivered
    return Page(records=records, cursor=encode_cursor(adapter.params, b_ids, b_date), status=200)


if __name__ == "__main__":
    # cursor round-trip + binding self-checks (no network)
    _p = {"kind": "advanced", "area": 2}
    _c = encode_cursor(_p, [15231022, 15231019], date(2025, 3, 25))
    assert verify_cursor(_p, _c) == ([15231022, 15231019], date(2025, 3, 25))
    try:
        verify_cursor({"kind": "records"}, _c)
        raise SystemExit("binding check failed: wrong params verified")
    except ValueError:
        pass
    log.info("cursor self-checks OK (%d chars)", len(_c))

    from constants import Operador, Columna

    GACETA = Gaceta.SOLICITUDES_DE_MARCAS_AVISOS_Y_NOMBRES_COMERCIALES_PRESENTADAS_ANTE_EL_INSTITUTO

    async def smoke() -> None:
        adapter = advanced_adapter(
            area=Area.MARCAS, gacetas=[GACETA],
            secciones=[Seccion.SOLICITUDES_DE_MARCAS_100188],
            datos=[Dato(operador=Operador.EMPTY, columna=Columna.CLASE, valor="42")],
        )
        # default: a light bounded window (1 page). Pass "all" to drain everything.
        full = "all" in sys.argv
        kw: dict[str, Any] = {} if full else {
            "fecha_desde": mexico_today() - timedelta(days=20),
            "fecha_hasta": mexico_today(),
        }
        async with aiohttp.ClientSession() as session:
            env = await search_all(session, adapter, **kw)
            p = env["pagination"]
            log.info("search_all: requests=%s returned=%s truncated=%s",
                     p["requests"], p["returned"], p["truncated"])

    asyncio.run(smoke())

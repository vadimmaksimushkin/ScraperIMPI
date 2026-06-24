import asyncio
import logging
import os
import sys
import uuid
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import (
    API_PREFIX,
    ARCHIVE_DIR,
    ARCHIVE_TTL_HOURS,
    CLEANUP_INTERVAL_SECONDS,
)
from base_search import AntiforgeryError
from home_download import download_archive
import copies_search
import records_search
import advanced_search
import images
import export
import paginator
from constants import (
    Area,
    Columna,
    Dato,
    Gaceta,
    Operador,
    Seccion,
    GACETA_COLUMNAS,
    SECCION_COLUMNAS,
)

log = logging.getLogger("siga.api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)


class ArchiveType(str, Enum):
    xlsx = "xlsx"
    pdf = "pdf"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Hold references to fire-and-forget tasks so they aren't garbage-collected mid-run.
_tasks: set[asyncio.Task[None]] = set()


def _spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _job_status_payload(
    job_id: str,
    rows: list[Any],
    download_prefix: str,
    empty_message: str,
) -> dict[str, Any]:
    """Shared poll-response builder for the home and export download jobs."""
    statuses = [r["status"] for r in rows]
    if "pending" in statuses:
        return {"job_id": job_id, "status": "running"}

    ready = [r for r in rows if r["status"] == "ready"]
    failed = [r for r in rows if r["status"] == "failed"]

    if ready:
        files: list[dict[str, Any]] = [
            {
                "token": r["token"],
                "type": r["type"],
                "filename": r["source_filename"],
                "size_bytes": r["size_bytes"],
                "expires_at": r["expires_at"],
                "download_url": f"{download_prefix}/{r['token']}",
            }
            for r in ready
        ]
        result: dict[str, Any] = {"job_id": job_id, "status": "done", "files": files}
        if failed:
            result["errors"] = [
                {"type": r["type"], "error": r["error"]} for r in failed
            ]
        return result

    if failed:
        return {
            "job_id": job_id,
            "status": "failed",
            "errors": [{"type": r["type"], "error": r["error"]} for r in failed],
        }

    return {"job_id": job_id, "status": "done", "message": empty_message, "files": []}


async def _run_job(job_id: str, items: list[tuple[str, str]]) -> None:
    """Fetch each requested archive type, updating its download row as it lands.
    One aiohttp session per job keeps the antiforgery cookie jar free of the
    cross-talk that a session shared between concurrent jobs would cause."""
    async with aiohttp.ClientSession() as session:
        for token, atype in items:
            try:
                path = await download_archive(
                    session=session, type=atype, download_dir=ARCHIVE_DIR  # type: ignore[arg-type]
                )
                if path is None:
                    await db.mark_empty(token)
                    continue
                expires_at = (
                    _utcnow() + timedelta(hours=ARCHIVE_TTL_HOURS)
                ).isoformat()
                await db.mark_ready(
                    token,
                    source_filename=path.name,
                    stored_path=str(path),
                    size_bytes=path.stat().st_size,
                    expires_at=expires_at,
                )
            except Exception as exc:
                log.exception("download failed job=%s type=%s", job_id, atype)
                await db.mark_failed(token, str(exc))


async def _run_export_job(
    job_id: str, token: str, kind: str, format: str, ids: list[int]
) -> None:
    """Stream one export to disk, then flip its download row to ready/failed.
    One aiohttp session per job, same as _run_job."""
    async with aiohttp.ClientSession() as session:
        try:
            path = await export.export_to_disk(
                session=session, kind=kind, format=format, ids=ids, download_dir=ARCHIVE_DIR
            )
            expires_at = (_utcnow() + timedelta(hours=ARCHIVE_TTL_HOURS)).isoformat()
            await db.mark_ready(
                token,
                source_filename=path.name,
                stored_path=str(path),
                size_bytes=path.stat().st_size,
                expires_at=expires_at,
            )
        except Exception as exc:
            log.exception("export failed job=%s kind=%s format=%s", job_id, kind, format)
            await db.mark_failed(token, str(exc))


async def _sweep_expired() -> None:
    rows = await db.due_for_cleanup()
    for row in rows:
        if row["stored_path"]:
            Path(row["stored_path"]).unlink(missing_ok=True)
        await db.mark_deleted(row["token"])
    if rows:
        log.info("cleanup: removed %d expired archive(s)", len(rows))


async def _cleanup_loop() -> None:
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            await _sweep_expired()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the sweep loop die
            log.exception("cleanup sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once per worker process.
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    await db.init_db()
    cleanup = asyncio.create_task(_cleanup_loop())
    log.info("siga-api up | archive_dir=%s ttl=%sh", ARCHIVE_DIR, ARCHIVE_TTL_HOURS)
    try:
        yield
    finally:
        cleanup.cancel()
        try:
            await cleanup
        except asyncio.CancelledError:
            pass


app = FastAPI(title="SIGA Direct API", lifespan=lifespan)


@app.exception_handler(asyncio.TimeoutError)
async def _upstream_timeout(request: Request, exc: asyncio.TimeoutError) -> JSONResponse:
    """A slow/large SIGA fetch can time out; surface it as a clean 504 JSON body
    instead of Starlette's bare-text 500 (which the JSON client can't parse)."""
    log.warning("upstream timeout: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=504, content={"detail": "upstream SIGA request timed out"}
    )


@app.exception_handler(AntiforgeryError)
async def _antiforgery_blocked(request: Request, exc: AntiforgeryError) -> JSONResponse:
    """The /antiforgery/token handshake was throttled (anti-bot 403). Surface a
    clean 503 instead of a bare 500 so the client can back off and retry."""
    log.warning("antiforgery handshake blocked: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "SIGA antiforgery handshake throttled — retry shortly"},
    )


@app.exception_handler(aiohttp.ClientError)
async def _upstream_client_error(
    request: Request, exc: aiohttp.ClientError
) -> JSONResponse:
    # aiohttp.ServerTimeoutError is both a ClientError and a TimeoutError; keep it a 504.
    if isinstance(exc, asyncio.TimeoutError):
        log.warning("upstream timeout: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=504, content={"detail": "upstream SIGA request timed out"}
        )
    log.warning("upstream client error: %s", exc)
    return JSONResponse(
        status_code=502, content={"detail": f"upstream SIGA error: {exc}"}
    )


@app.get(f"{API_PREFIX}/home/today")
async def home_today(
    request: Request,
    type: list[ArchiveType] = Query(default=[ArchiveType.xlsx]),
) -> JSONResponse:
    """Kick off today's archive download(s) and return a 202 + a poll URL.
    The actual SIGA fetch can take minutes, so it runs in the background."""
    types = list(dict.fromkeys(t.value for t in type))  # dedupe, preserve order
    job_id = uuid.uuid4().hex
    items = [(uuid.uuid4().hex, t) for t in types]
    await db.add_pending_downloads(job_id, items)

    body: dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "status_url": f"{API_PREFIX}/home/jobs/{job_id}",
    }
    await db.log_upstream(
        request={
            "method": request.method,
            "path": request.url.path,
            "query": request.url.query,
            "types": types,
        },
        response_meta={"http_status": 202, **body},
    )
    _spawn(_run_job(job_id, items))
    return JSONResponse(status_code=202, content=body)


@app.get(f"{API_PREFIX}/home/jobs/{{job_id}}")
async def home_job_status(job_id: str) -> dict[str, Any]:
    rows = await db.get_job(job_id)
    if not rows:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return _job_status_payload(
        job_id, rows, f"{API_PREFIX}/home/download", "No new updates for today yet"
    )


@app.get(f"{API_PREFIX}/home/download/{{token}}")
async def home_download_file(token: str) -> FileResponse:
    row = await db.get_download(token)
    if not row or row["status"] != "ready" or row["deleted_at"]:
        raise HTTPException(status_code=404, detail="not found")
    if row["expires_at"] and row["expires_at"] < _utcnow().isoformat():
        raise HTTPException(status_code=404, detail="expired")
    path = Path(row["stored_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing")
    # FileResponse streams off disk in chunks; the archive is never read into RAM.
    return FileResponse(
        path, media_type="application/zip", filename=row["source_filename"]
    )


def _resolve_area(area_id: int) -> Area:
    try:
        return Area(area_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"unknown area id: {area_id}")


def _resolve_gaceta(area: Area, gaceta_id: int) -> Gaceta:
    # scope by area so duplicate id_gaceta values across areas can't collide
    gaceta = next(
        (g for g in Gaceta if g.area is area and g.id_gaceta == gaceta_id), None
    )
    if gaceta is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown gaceta id {gaceta_id} for area {area.name}",
        )
    return gaceta


def _count_entries(res: Any) -> int | None:
    """GetEjemplares returns data as a list; GetEjemplaresArrayByFecha returns it
    as a dict of lists. Size by type; None if the body isn't the expected shape."""
    if not isinstance(res, dict):
        return None
    data = res.get("data") # type: ignore
    if isinstance(data, list):
        return len(data) # type: ignore
    if isinstance(data, dict):
        return sum(len(v) for v in data.values() if isinstance(v, list)) # type: ignore
    return 0


def _count_images(res: Any) -> int | None:
    """GetImagenArray returns data as {imagenBase64: [...]}. None if not that shape."""
    if not isinstance(res, dict):
        return None
    data = res.get("data")
    if not isinstance(data, dict):
        return None
    imgs = data.get("imagenBase64")
    return len(imgs) if isinstance(imgs, list) else 0


async def _paginate(
    adapter: paginator.Adapter,
    request_meta: dict[str, Any],
    *,
    all_: bool,
    cursor: str | None,
    fecha_desde: date | None,
    fecha_hasta: date | None,
) -> JSONResponse:
    """Drive the cap-defeating paginator for a search endpoint. Paged mode is
    entered with `?cursor=`: an EMPTY cursor returns the first page (+ a token),
    and each returned token fetches the next page. `?all=true` instead drains every
    window into a single envelope. Both walk the date range downward and dedup the
    boundary day (see paginator.py). The client must keep the query stable across
    pages — the cursor binds the search criteria, and its stop_date drives
    fecha_hasta. A ValueError (malformed/mismatched cursor, or an upstream
    input_validation rejection) becomes a 400."""
    async with aiohttp.ClientSession() as session:
        try:
            if cursor is not None:                    # paged mode ("" => first page)
                page = await paginator.search_page(
                    session, adapter, cursor=cursor or None,
                    fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
                )
                content: dict[str, Any] = {
                    "data": page.records,
                    "pagination": {
                        "returned": len(page.records),
                        "truncated": page.cursor is not None,
                        "cursor": page.cursor,
                    },
                }
                status = page.status
            else:  # all_
                content = await paginator.search_all(
                    session, adapter,
                    fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
                )
                status = 200
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    response = JSONResponse(status_code=status, content=content)
    pg = content["pagination"]
    await db.log_upstream(
        request={**request_meta, "mode": "cursor" if cursor is not None else "all"},
        response_meta={
            "http_status": status,
            "num_entries": pg.get("returned"),
            "requests": pg.get("requests"),
            "truncated": pg.get("truncated"),
            "bytes": len(response.body),
        },
    )
    return response


@app.get(f"{API_PREFIX}/copies/search")
async def copies_search_endpoint(
    area: int,
    gaceta: int | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
    all_: bool = Query(default=False, alias="all"),
    cursor: str | None = None,
) -> JSONResponse:
    """Ejemplares search. Routes to GetEjemplares (by gaceta) or
    GetEjemplaresArrayByFecha (by date range) inside copies_search; SIGA's status
    and body are mirrored straight back to the caller (this is a test server).
    `?all=true` / `?cursor=` page via the paginator (ejemplares never hit the 15000
    cap, so all=true just returns the full set as a flat, deduped list)."""
    area_enum = _resolve_area(area)
    gaceta_enum = _resolve_gaceta(area_enum, gaceta) if gaceta is not None else None

    if all_ or cursor is not None:
        return await _paginate(
            paginator.copies_adapter(area=area_enum, gaceta=gaceta_enum),
            {"area": area, "gaceta": gaceta},
            all_=all_, cursor=cursor,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
        )

    try:
        async with aiohttp.ClientSession() as session:
            status, res = await copies_search.search(
                session=session,
                area=area_enum,
                gaceta=gaceta_enum,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                recaptcha=recaptcha,
            )
    except ValueError as exc:  # input_validation rejected the params
        raise HTTPException(status_code=400, detail=str(exc))

    response = JSONResponse(status_code=status, content=res)
    await db.log_upstream(
        request={
            "area": area,
            "gaceta": gaceta,
            "fecha_desde": str(fecha_desde) if fecha_desde else None,
            "fecha_hasta": str(fecha_hasta) if fecha_hasta else None,
        },
        response_meta={
            "http_status": status,
            "num_entries": _count_entries(res),
            "bytes": len(response.body),
        },
    )
    return response


@app.get(f"{API_PREFIX}/records/search")
async def records_search_endpoint(
    busqueda: int,
    area: int | None = None,
    gacetas: list[int] | None = Query(default=None),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
    all_: bool = Query(default=False, alias="all"),
    cursor: str | None = None,
) -> JSONResponse:
    """Fichas search (BusquedaFicha/GetFichas). busqueda is required; area and
    gacetas are optional but scoped together (both or neither). SIGA's status and
    body are mirrored straight back to the caller. `?all=true` drains past the
    15000-row cap by walking the date window; `?cursor=` returns one page + a
    continuation token."""
    area_enum = _resolve_area(area) if area is not None else None
    gaceta_enums: list[Gaceta] | None = None
    if gacetas is not None:
        if area_enum is None:
            raise HTTPException(status_code=400, detail="gacetas require area")
        gaceta_enums = [_resolve_gaceta(area_enum, gid) for gid in gacetas]

    if all_ or cursor is not None:
        return await _paginate(
            paginator.records_adapter(busqueda=busqueda, area=area_enum, gacetas=gaceta_enums),
            {"busqueda": busqueda, "area": area, "gacetas": gacetas},
            all_=all_, cursor=cursor,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
        )

    try:
        async with aiohttp.ClientSession() as session:
            status, res = await records_search.search(
                session=session,
                busqueda=busqueda,
                area=area_enum,
                gacetas=gaceta_enums,
                fecha_desde=fecha_desde,
                fecha_hasta=fecha_hasta,
                recaptcha=recaptcha,
            )
    except ValueError as exc:  # input_validation rejected the params
        raise HTTPException(status_code=400, detail=str(exc))

    response = JSONResponse(status_code=status, content=res)
    await db.log_upstream(
        request={
            "busqueda": busqueda,
            "area": area,
            "gacetas": gacetas,
            "fecha_desde": str(fecha_desde) if fecha_desde else None,
            "fecha_hasta": str(fecha_hasta) if fecha_hasta else None,
        },
        response_meta={
            "http_status": status,
            "num_entries": _count_entries(res),
            "bytes": len(response.body),
        },
    )
    return response


class DatoInput(BaseModel):
    columna: str                 # Columna enum name, e.g. "CLASE"
    operador: str = ""           # "" for the first dato; AND/OR/NOT for the next
    valor: str = ""              # for VALOR-kind columns
    fecha: date | None = None    # for FECHA-kind columns


class AdvancedSearchRequest(BaseModel):
    area: int
    datos: list[DatoInput]
    gacetas: list[int] = []
    secciones: list[int] = []
    fecha_desde: date | None = None
    fecha_hasta: date | None = None
    recaptcha: str = ""


def _resolve_seccion(area: Area, seccion_id: int) -> Seccion:
    # scope by area so duplicate id_seccion values across areas can't collide
    seccion = next(
        (s for s in Seccion if s.gaceta.area is area and s.id_seccion == seccion_id),
        None,
    )
    if seccion is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown seccion id {seccion_id} for area {area.name}",
        )
    return seccion


def _build_dato(dato: DatoInput) -> Dato:
    try:
        columna = Columna[dato.columna]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown columna: {dato.columna}")
    try:
        operador = Operador(dato.operador)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"unknown operador: {dato.operador!r}"
        )
    try:
        # Dato validates valor/fecha against the column's Kind on construction
        return Dato(
            operador=operador, columna=columna, valor=dato.valor, fecha=dato.fecha
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get(f"{API_PREFIX}/advanced/areas")
async def advanced_areas() -> list[dict[str, Any]]:
    """Selectable areas (the AREA_7/AREA_8 placeholders are excluded)."""
    return [
        {"id": a.value, "name": a.name} for a in Area if not a.name.startswith("AREA_")
    ]


@app.get(f"{API_PREFIX}/advanced/gacetas")
async def advanced_gacetas(
    area: list[int] = Query(default=[]),
) -> list[dict[str, Any]]:
    """Gacetas in the given area(s); all gacetas when no area is given."""
    if area:
        areas = {_resolve_area(a) for a in area}
        gacetas: Any = (g for g in Gaceta if g.area in areas)
    else:
        gacetas = iter(Gaceta)
    return sorted(
        ({"id": g.id_gaceta, "name": g.name} for g in gacetas),
        key=lambda row: row["name"],
    )


@app.get(f"{API_PREFIX}/advanced/secciones")
async def advanced_secciones(
    gaceta: list[int] = Query(default=[]),
) -> list[dict[str, Any]]:
    """Secciones within the given gaceta(s); all secciones when none given.
    (gaceta ids are globally unique.)"""
    if gaceta:
        known = {g.id_gaceta for g in Gaceta}
        unknown = [gid for gid in gaceta if gid not in known]
        if unknown:
            raise HTTPException(
                status_code=400, detail=f"unknown gaceta id(s): {unknown}"
            )
        wanted = set(gaceta)
        secciones: Any = (s for s in Seccion if s.gaceta.id_gaceta in wanted)
    else:
        secciones = iter(Seccion)
    return sorted(
        ({"id": s.id_seccion, "name": s.name} for s in secciones),
        key=lambda row: row["name"],
    )


@app.get(f"{API_PREFIX}/advanced/columnas")
async def advanced_columnas(
    gaceta: list[int] = Query(default=[]),
    seccion: list[int] = Query(default=[]),
) -> list[dict[str, Any]]:
    """Columnas searchable across ALL the given gacetas and secciones
    (intersection, matching the structured-search validation). All columnas
    when neither is given. `name` is what advanced/search expects in a dato."""
    valid: set[Columna] | None = None

    if gaceta:
        by_gaceta = {g.id_gaceta: g for g in Gaceta}
        for gid in gaceta:
            g = by_gaceta.get(gid)
            if g is None:
                raise HTTPException(status_code=400, detail=f"unknown gaceta id: {gid}")
            cols = GACETA_COLUMNAS.get(g, set())
            valid = cols if valid is None else (valid & cols)

    if seccion:
        by_seccion = {s.id_seccion: s for s in Seccion}
        for sid in seccion:
            s = by_seccion.get(sid)
            if s is None:
                raise HTTPException(
                    status_code=400, detail=f"unknown seccion id: {sid}"
                )
            cols = SECCION_COLUMNAS.get(s, set())
            valid = cols if valid is None else (valid & cols)

    if valid is None:
        valid = set(Columna)

    return sorted(
        (
            {
                "name": c.name,
                "label": c.columna,
                "kind": c.kind.value if hasattr(c.kind, "value") else str(c.kind),
            }
            for c in valid
        ),
        key=lambda row: row["label"],
    )


@app.post(f"{API_PREFIX}/advanced/search")
async def advanced_search_endpoint(
    body: AdvancedSearchRequest,
    all_: bool = Query(default=False, alias="all"),
    cursor: str | None = None,
) -> JSONResponse:
    """Estructurada search (BusquedaEstructurada/GetSearchEstructurada). POST
    because valor can be large and datos is structured. SIGA's status and body
    are mirrored straight back to the caller. `?all=true` drains past the 15000-row
    cap by walking the date window; `?cursor=` returns one page + a continuation
    token (both as query params, the body still carries the search)."""
    area_enum = _resolve_area(body.area)
    gaceta_enums = [_resolve_gaceta(area_enum, gid) for gid in body.gacetas]
    seccion_enums = [_resolve_seccion(area_enum, sid) for sid in body.secciones]
    datos = [_build_dato(dato) for dato in body.datos]

    if all_ or cursor is not None:
        return await _paginate(
            paginator.advanced_adapter(
                area=area_enum, gacetas=gaceta_enums,
                secciones=seccion_enums, datos=datos,
            ),
            {"kind": "advanced", **body.model_dump(mode="json", exclude={"recaptcha"})},
            all_=all_, cursor=cursor,
            fecha_desde=body.fecha_desde, fecha_hasta=body.fecha_hasta,
        )

    try:
        async with aiohttp.ClientSession() as session:
            status, res = await advanced_search.search(
                session=session,
                area=area_enum,
                gacetas=gaceta_enums,
                secciones=seccion_enums,
                datos=datos,
                fecha_desde=body.fecha_desde,
                fecha_hasta=body.fecha_hasta,
                recaptcha=body.recaptcha,
            )
    except ValueError as exc:  # input_validation rejected the params
        raise HTTPException(status_code=400, detail=str(exc))

    response = JSONResponse(status_code=status, content=res)
    await db.log_upstream(
        request=body.model_dump(mode="json"),
        response_meta={
            "http_status": status,
            "num_entries": _count_entries(res),
            "bytes": len(response.body),
        },
    )
    return response


@app.get(f"{API_PREFIX}/images/{{id_ficha}}")
async def images_endpoint(id_ficha: int) -> JSONResponse:
    """Per-ficha images (DescargaEjemplares/GetImagenArray). SIGA's status and
    body are mirrored straight back: data.imagenBase64 is the base64 image list
    ([] / no images for a ficha without a logo). One ficha per call — the
    frontend lazy-loads these one at a time (a ficha-by-ficha sweep of a large
    search would be thousands of calls)."""
    try:
        async with aiohttp.ClientSession() as session:
            status, res = await images.fetch_images(session, id_ficha)
    except ValueError as exc:  # input_validation rejected the id
        raise HTTPException(status_code=400, detail=str(exc))

    response = JSONResponse(status_code=status, content=res)
    await db.log_upstream(
        request={"id_ficha": id_ficha},
        response_meta={
            "http_status": status,
            "num_images": _count_images(res),
            "bytes": len(response.body),
        },
    )
    return response


class ExportRequest(BaseModel):
    format: str          # copies: pdf|xml|xlsx · fichas: pdf|xlsx
    id: list[int]        # ejemplar i_id(s) for copies, fichaId(s) for fichas


@app.post(f"{API_PREFIX}/export/copies")
async def export_copies_endpoint(body: ExportRequest, request: Request) -> JSONResponse:
    """Ejemplares export. Large + slow (one ejemplar PDF is ~60-100 MB, a batch
    can exceed several GB), so it runs as a background job: 202 + a poll URL, then
    a download URL, exactly like /home/today."""
    try:
        export.resolve_copies(body.format, body.id)  # validate format/ids/xml-arity
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    fmt = body.format.lower()
    job_id = uuid.uuid4().hex
    token = uuid.uuid4().hex
    await db.add_pending_downloads(job_id, [(token, f"copies_{fmt}")])

    resp_body: dict[str, Any] = {
        "job_id": job_id,
        "status": "running",
        "status_url": f"{API_PREFIX}/export/jobs/{job_id}",
    }
    await db.log_upstream(
        request={"kind": "copies", "format": fmt, "id": body.id},
        response_meta={"http_status": 202, **resp_body},
    )
    _spawn(_run_export_job(job_id, token, "copies", fmt, body.id))
    return JSONResponse(status_code=202, content=resp_body)


@app.post(f"{API_PREFIX}/export/fichas")
async def export_fichas_endpoint(body: ExportRequest) -> StreamingResponse:
    """Fichas export (FichasGaceta). Quick, so it streams the file straight back
    rather than going through a job. The aiohttp session is held open until the
    stream drains."""
    try:
        export.resolve_fichas(body.format, body.id)  # validate format/ids
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    fmt = body.format.lower()
    session = aiohttp.ClientSession()
    try:
        res = await export.request_export(session, kind="fichas", format=fmt, ids=body.id)
    except Exception:
        await session.close()
        raise
    if res.status != 200:
        data = await res.read()
        await session.close()
        raise HTTPException(
            status_code=502, detail=f"upstream {res.status}: {data[:200]!r}"
        )

    filename = export.get_filename_from_headers(res, f"fichas_{fmt}")
    media = res.headers.get("Content-Type") or export.MEDIA_TYPES.get(
        fmt, "application/octet-stream"
    )

    async def streamer():
        try:
            async for chunk in res.content.iter_chunked(1 << 16):
                yield chunk
        finally:
            await session.close()

    await db.log_upstream(
        request={"kind": "fichas", "format": fmt, "id": body.id},
        response_meta={"http_status": 200, "filename": filename},
    )
    return StreamingResponse(
        streamer(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get(f"{API_PREFIX}/export/jobs/{{job_id}}")
async def export_job_status(job_id: str) -> dict[str, Any]:
    rows = await db.get_job(job_id)
    if not rows:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return _job_status_payload(
        job_id, rows, f"{API_PREFIX}/export/download", "export produced no file"
    )


@app.get(f"{API_PREFIX}/export/download/{{token}}")
async def export_download_file(token: str) -> FileResponse:
    row = await db.get_download(token)
    if not row or row["status"] != "ready" or row["deleted_at"]:
        raise HTTPException(status_code=404, detail="not found")
    if row["expires_at"] and row["expires_at"] < _utcnow().isoformat():
        raise HTTPException(status_code=404, detail="expired")
    path = Path(row["stored_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(
        path,
        media_type=export.media_type_for_filename(row["source_filename"]),
        filename=row["source_filename"],
    )


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("SIGA_HOST", "0.0.0.0"),
        port=int(os.getenv("SIGA_PORT", "8000")),
        workers=int(os.getenv("SIGA_WORKERS", "4")),
    )

import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
from config import (
    API_PREFIX,
    ARCHIVE_DIR,
    ARCHIVE_TTL_HOURS,
    CLEANUP_INTERVAL_SECONDS,
)
from home_download import download_archive
import copies_search
import records_search
import advanced_search
from constants import Area, Columna, Dato, Gaceta, Operador, Seccion

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
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------
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
            except Exception as exc:  # noqa: BLE001 - record it, keep other types going
                log.exception("download failed job=%s type=%s", job_id, atype)
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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
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

    statuses = [r["status"] for r in rows]
    if "pending" in statuses:
        return {"job_id": job_id, "status": "running"}

    ready = [r for r in rows if r["status"] == "ready"]
    failed = [r for r in rows if r["status"] == "failed"]

    if ready:
        files = [
            {
                "token": r["token"],
                "type": r["type"],
                "filename": r["source_filename"],
                "size_bytes": r["size_bytes"],
                "expires_at": r["expires_at"],
                "download_url": f"{API_PREFIX}/home/download/{r['token']}",
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

    # everything came back empty
    return {
        "job_id": job_id,
        "status": "done",
        "message": "No new updates for today yet",
        "files": [],
    }


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


# ---------------------------------------------------------------------------
# Copies (Ejemplares) search
# ---------------------------------------------------------------------------
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
    data = res.get("data")
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        return sum(len(v) for v in data.values() if isinstance(v, list))
    return 0


@app.get(f"{API_PREFIX}/copies/search")
async def copies_search_endpoint(
    area: int,
    gaceta: int | None = None,
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> JSONResponse:
    """Ejemplares search. Routes to GetEjemplares (by gaceta) or
    GetEjemplaresArrayByFecha (by date range) inside copies_search; SIGA's status
    and body are mirrored straight back to the caller (this is a test server)."""
    area_enum = _resolve_area(area)
    gaceta_enum = _resolve_gaceta(area_enum, gaceta) if gaceta is not None else None

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


# ---------------------------------------------------------------------------
# Records (Fichas) search
# ---------------------------------------------------------------------------
@app.get(f"{API_PREFIX}/records/search")
async def records_search_endpoint(
    busqueda: int,
    area: int | None = None,
    gacetas: list[int] | None = Query(default=None),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    recaptcha: str = "",
) -> JSONResponse:
    """Fichas search (BusquedaFicha/GetFichas). busqueda is required; area and
    gacetas are optional but scoped together (both or neither). SIGA's status and
    body are mirrored straight back to the caller."""
    area_enum = _resolve_area(area) if area is not None else None
    gaceta_enums: list[Gaceta] | None = None
    if gacetas is not None:
        if area_enum is None:
            raise HTTPException(status_code=400, detail="gacetas require area")
        gaceta_enums = [_resolve_gaceta(area_enum, gid) for gid in gacetas]

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


# ---------------------------------------------------------------------------
# Advanced (Estructurada) search -- POST: valor can be large, datos is structured
# ---------------------------------------------------------------------------
class Term(BaseModel):
    columna: str                 # Columna enum name, e.g. "CLASE"
    operador: str = ""           # "" for the first term; AND/OR/NOT for the next
    valor: str = ""              # for VALOR-kind columns
    fecha: date | None = None    # for FECHA-kind columns


class AdvancedSearchRequest(BaseModel):
    area: int
    datos: list[Term]
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


def _build_dato(term: Term) -> Dato:
    try:
        columna = Columna[term.columna]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"unknown columna: {term.columna}")
    try:
        operador = Operador(term.operador)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"unknown operador: {term.operador!r}"
        )
    try:
        # Dato validates valor/fecha against the column's Kind on construction
        return Dato(
            operador=operador, columna=columna, valor=term.valor, fecha=term.fecha
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
async def advanced_gacetas(area: int) -> list[dict[str, Any]]:
    """Gacetas in the given area — numeric ids, as the search endpoint expects."""
    area_enum = _resolve_area(area)
    return sorted(
        ({"id": g.id_gaceta, "name": g.name} for g in Gaceta if g.area is area_enum),
        key=lambda row: row["name"],
    )


@app.get(f"{API_PREFIX}/advanced/secciones")
async def advanced_secciones(gaceta: int) -> list[dict[str, Any]]:
    """Secciones within the given gaceta (gaceta ids are globally unique)."""
    gaceta_enum = next((g for g in Gaceta if g.id_gaceta == gaceta), None)
    if gaceta_enum is None:
        raise HTTPException(status_code=400, detail=f"unknown gaceta id: {gaceta}")
    return sorted(
        ({"id": s.id_seccion, "name": s.name} for s in Seccion if s.gaceta is gaceta_enum),
        key=lambda row: row["name"],
    )


@app.post(f"{API_PREFIX}/advanced/search")
async def advanced_search_endpoint(body: AdvancedSearchRequest) -> JSONResponse:
    """Estructurada search (BusquedaEstructurada/GetSearchEstructurada). POST
    because valor can be large and datos is structured. SIGA's status and body
    are mirrored straight back to the caller."""
    area_enum = _resolve_area(body.area)
    gaceta_enums = [_resolve_gaceta(area_enum, gid) for gid in body.gacetas]
    seccion_enums = [_resolve_seccion(area_enum, sid) for sid in body.secciones]
    datos = [_build_dato(term) for term in body.datos]

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


# ---------------------------------------------------------------------------
# Static test console (index.html + script.js), served at GET /.
# Mounted last so it only catches paths the API routes and /docs don't.
# ---------------------------------------------------------------------------
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.getenv("SIGA_HOST", "0.0.0.0"),
        port=int(os.getenv("SIGA_PORT", "8000")),
        workers=int(os.getenv("SIGA_WORKERS", "4")),
    )

import asyncio
import logging
import sys
from typing import Any

import aiohttp

from base_search import (
    BASE,
    RequestMethods,
    request_no_token,
)

log = logging.getLogger("siga.images")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

# Per-ficha images, returned as base64 strings inside the JSON body (plain
# integer id, no AES). See debug/ReferencesAndFiles/SIGA_API_REFERENCE.md §13.
# One call per ficha -> meant to be lazy-loaded one at a time by the frontend.
# Proven 2026-06-23: this endpoint does NOT validate the antiforgery token, so we
# call it token-less (no /antiforgery/token GET) to avoid the anti-bot throttling
# that endpoint when the frontend auto-loads many images.
URL = f"{BASE}/api/DescargaEjemplares/GetImagenArray"

INT64_MAX = 2**63 - 1  # widest fixed-width integer any backend could represent


def input_validation(ficha_id: int) -> tuple[bool, str]:
    # id_ficha is required: a positive int (not bool) the backend can represent
    if isinstance(ficha_id, bool) or not isinstance(ficha_id, int):  # type: ignore
        return False, "id_ficha must be the type int"
    if ficha_id < 1:
        return False, "id_ficha must be a positive int"
    if ficha_id > INT64_MAX:
        return False, "id_ficha is too large for the backend to represent"
    return True, "OK"


async def fetch_images(
    session: aiohttp.ClientSession,
    ficha_id: int,
) -> tuple[int, Any]:
    """POST GetImagenArray {id: fichaId}. Returns (status, parsed_json_or_text),
    mirroring the search modules. The body's data.imagenBase64 is the base64
    image list."""
    ok, message = input_validation(ficha_id)
    if not ok:
        raise ValueError(message)
    return await request_no_token(
        session=session,
        method=RequestMethods.POST,
        url=URL,
        payload={"id": ficha_id},
    )


if __name__ == "__main__":

    async def test() -> None:
        ficha_id = 15253160  # SHOPPERX, countImagen 1
        async with aiohttp.ClientSession() as session:
            status, res = await fetch_images(session, ficha_id)
            data = res.get("data") if isinstance(res, dict) else None
            imgs = data.get("imagenBase64") if isinstance(data, dict) else None
            n = len(imgs) if isinstance(imgs, list) else 0
            log.info("status=%s images=%d", status, n)

    asyncio.run(test())

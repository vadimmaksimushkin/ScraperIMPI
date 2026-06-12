"""Adversarial tests for home_download.

These probe the home (Inicio) archive download path hard: the
Content-Disposition parser, the request wiring, the status/sentinel handling
and the filesystem write. Several sections assert the *intended* (correct)
behaviour and therefore currently FAIL/ERROR — they document real bugs
(no RFC-5987 decoding, no filename* precedence, path-traversal write, set-based
order loss, mutable default arg). That's deliberate, mirroring the hardening
contract tests in test_copies_search.py / test_records_search.py.

No network and no real files: aiohttp responses/sessions are faked and every
write is redirected into pytest's tmp_path. Coroutines are driven with
asyncio.run() because pytest-asyncio is not installed.
"""

import asyncio
import inspect
from pathlib import Path

import orjson
import pytest

import home_download
from base_search import HEADERS_DEFAULT, TokenPair
from constants import RequestMethods
from home_download import (
    URL_PDF,
    URL_XLSX,
    download_archive,
    download_request,
    download_todays_archive,
    get_filename_from_headers,
)

# The exact body the server returns (404) when there is nothing to download.
NO_FILES_404 = b"No se encontraron archivos PDF para descargar."


# ===========================================================================
# Fakes / helpers
# ===========================================================================
class FakeResponse:
    """Stands in for aiohttp.ClientResponse: status, headers, async read()."""

    def __init__(
        self, status: int = 200, headers: dict | None = None, body: bytes = b""
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self._body = body

    async def read(self) -> bytes:
        return self._body


class FakeSession:
    """Captures the kwargs passed to .request and returns a canned response."""

    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.captured: dict | None = None

    async def request(self, **kwargs) -> FakeResponse:
        self.captured = kwargs
        return self._response


def cd(value: str) -> FakeResponse:
    """A response carrying just a Content-Disposition header."""
    return FakeResponse(headers={"Content-Disposition": value})


def run(coro):
    return asyncio.run(coro)


# ===========================================================================
# get_filename_from_headers: baseline (these should PASS)
# ===========================================================================
def test_filename_quoted() -> None:
    assert (
        get_filename_from_headers(cd('attachment; filename="gaceta_today.xlsx"'))
        == "gaceta_today.xlsx"
    )


def test_filename_unquoted() -> None:
    assert (
        get_filename_from_headers(cd("attachment; filename=gaceta_today.xlsx"))
        == "gaceta_today.xlsx"
    )


def test_filename_among_other_params() -> None:
    assert (
        get_filename_from_headers(cd('attachment; filename="real.xlsx"; size=123'))
        == "real.xlsx"
    )


def test_missing_header_returns_fallback() -> None:
    assert get_filename_from_headers(FakeResponse(), fallback="fb.bin") == "fb.bin"


def test_empty_header_returns_fallback() -> None:
    assert get_filename_from_headers(cd(""), fallback="fb.bin") == "fb.bin"


def test_default_fallback_value() -> None:
    assert get_filename_from_headers(FakeResponse()) == "filename_was_not_returned"


# ===========================================================================
# get_filename_from_headers: INTENDED behaviour — currently FAILS.
# The regex captures bytes verbatim: no percent-decoding, no filename*
# precedence, breaks on quoted semicolons, is case-sensitive, and is confused
# by whitespace / lowercase encoding labels.
# ===========================================================================
def test_rfc5987_percent_decoded() -> None:
    # filename*=UTF-8''gaceta%20marcas.xlsx must decode to a real space.
    name = get_filename_from_headers(
        cd("attachment; filename*=UTF-8''gaceta%20marcas.xlsx")
    )
    assert name == "gaceta marcas.xlsx"


def test_filename_star_takes_precedence() -> None:
    # RFC 6266: filename* wins over filename; re.search grabs the first match.
    name = get_filename_from_headers(
        cd("attachment; filename=\"plain.xlsx\"; filename*=UTF-8''fancy%20.xlsx")
    )
    assert name == "fancy .xlsx"


def test_quoted_semicolon_kept() -> None:
    # A semicolon inside the quotes is part of the name, not a delimiter.
    assert get_filename_from_headers(cd('attachment; filename="a;b.xlsx"')) == "a;b.xlsx"


def test_param_name_case_insensitive() -> None:
    # HTTP parameter names are case-insensitive.
    assert get_filename_from_headers(cd('attachment; FILENAME="up.xlsx"')) == "up.xlsx"


def test_whitespace_around_equals() -> None:
    assert (
        get_filename_from_headers(cd('attachment; filename = "spaced.xlsx"'))
        == "spaced.xlsx"
    )


def test_lowercase_encoding_label_stripped() -> None:
    # The encoding label (utf-8'') must be stripped regardless of case.
    assert (
        get_filename_from_headers(cd("attachment; filename*=utf-8''doc.xlsx"))
        == "doc.xlsx"
    )


# ===========================================================================
# get_filename_from_headers: SECURITY — a server-controlled name must never
# carry path separators or traversal. INTENDED behaviour — currently FAILS.
# ===========================================================================
@pytest.mark.parametrize(
    "evil",
    [
        'attachment; filename="../../../../etc/passwd"',
        'attachment; filename="..\\..\\windows\\system32\\evil.dll"',
        'attachment; filename="/etc/cron.d/payload"',
    ],
)
def test_no_path_separators_in_parsed_name(evil: str) -> None:
    name = get_filename_from_headers(cd(evil))
    assert "/" not in name and "\\" not in name and ".." not in name


# ===========================================================================
# download_request: header / method / payload / cookie wiring
# ===========================================================================
def _patch_token(monkeypatch, request_token: str = "reqtok123") -> None:
    async def fake_fetch(session):
        return TokenPair("AF-COOKIE", "af-cookie-value", request_token)

    monkeypatch.setattr(home_download, "fetch_token_pair", fake_fetch)


def test_download_request_wires_everything(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeSession(FakeResponse(200))
    res = run(download_request(session, URL_XLSX, RequestMethods.POST, {"a": 1}))
    cap = session.captured
    assert res.status == 200
    assert cap["method"] == "POST"
    assert cap["url"] == URL_XLSX
    assert cap["headers"]["x-xsrf-token"] == "reqtok123"
    assert cap["headers"]["Content-Type"] == "application/json"
    for key in HEADERS_DEFAULT:
        assert key in cap["headers"]
    assert cap["data"] == orjson.dumps({"a": 1})
    assert cap["cookies"] == TokenPair("AF-COOKIE", "af-cookie-value", "reqtok123").cookies


def test_download_request_default_method_is_post(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeSession(FakeResponse(204))
    run(download_request(session, URL_PDF))
    assert session.captured["method"] == "POST"


def test_download_request_honours_method(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeSession(FakeResponse(200))
    run(download_request(session, URL_PDF, RequestMethods.GET))
    assert session.captured["method"] == "GET"


def test_download_request_empty_default_payload(monkeypatch) -> None:
    _patch_token(monkeypatch)
    session = FakeSession(FakeResponse(200))
    run(download_request(session, URL_XLSX))
    assert session.captured["data"] == orjson.dumps({})


def test_download_request_no_mutable_default_payload() -> None:
    # `payload: dict = {}` is a shared mutable default (anti-pattern). The
    # intended signature uses an immutable sentinel (None). INTENDED — FAILS.
    default = inspect.signature(download_request).parameters["payload"].default
    assert default is None


# ===========================================================================
# download_archive: status handling + filesystem write
# ===========================================================================
@pytest.fixture
def patch_download(monkeypatch, tmp_path):
    """Redirect writes into tmp_path/gacetas and stub the network layer.

    Yields (gacetas_dir, set_response) where set_response(FakeResponse) wires
    what the (faked) download_request returns.
    """
    gacetas = tmp_path / "gacetas"
    monkeypatch.setattr(home_download, "DOWNLOAD_PATH", gacetas)

    def set_response(response: FakeResponse) -> None:
        async def fake_request(**kwargs):
            return response

        monkeypatch.setattr(home_download, "download_request", fake_request)

    return gacetas, set_response


def test_download_archive_writes_header_name(patch_download) -> None:
    gacetas, set_response = patch_download
    set_response(
        FakeResponse(200, {"Content-Disposition": 'attachment; filename="g.xlsx"'}, b"DATA")
    )
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert path == gacetas / "g.xlsx"
    assert path.read_bytes() == b"DATA"


def test_download_archive_fallback_name(patch_download) -> None:
    gacetas, set_response = patch_download
    set_response(FakeResponse(200, {}, b"X"))
    path = run(download_archive(None, "pdf"))  # type: ignore[arg-type]
    assert path.parent == gacetas
    assert path.name.startswith("archive_") and path.name.endswith(".pdf")


def test_download_archive_empty_body_still_writes(patch_download) -> None:
    _, set_response = patch_download
    set_response(FakeResponse(200, {"Content-Disposition": 'filename="empty.xlsx"'}, b""))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert path.read_bytes() == b""


def test_download_archive_404_sentinel_returns_none(patch_download) -> None:
    _, set_response = patch_download
    set_response(FakeResponse(404, {}, NO_FILES_404))
    assert run(download_archive(None, "pdf")) is None  # type: ignore[arg-type]


def test_download_archive_404_writes_nothing(patch_download) -> None:
    gacetas, set_response = patch_download
    set_response(FakeResponse(404, {}, NO_FILES_404))
    run(download_archive(None, "pdf"))  # type: ignore[arg-type]
    assert not gacetas.exists() or list(gacetas.iterdir()) == []


def test_download_archive_xlsx_keys_off_pdf_worded_sentinel(patch_download) -> None:
    # Quirk: the 404 sentinel says "PDF" even on the xlsx export path.
    _, set_response = patch_download
    set_response(FakeResponse(404, {}, NO_FILES_404))
    assert run(download_archive(None, "xlsx")) is None  # type: ignore[arg-type]


def test_download_archive_404_other_body_raises(patch_download) -> None:
    _, set_response = patch_download
    set_response(FakeResponse(404, {}, b"something else"))
    with pytest.raises(RuntimeError):
        run(download_archive(None, "xlsx"))  # type: ignore[arg-type]


def test_download_archive_500_raises(patch_download) -> None:
    _, set_response = patch_download
    set_response(FakeResponse(500, {}, b"boom"))
    with pytest.raises(RuntimeError):
        run(download_archive(None, "xlsx"))  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["docx", "txt", "XLSX", "", "json", "pdf "])
def test_download_archive_invalid_type_raises(patch_download, bad: str) -> None:
    _, set_response = patch_download
    set_response(FakeResponse(200, {}, b"x"))
    with pytest.raises(ValueError):
        run(download_archive(None, bad))  # type: ignore[arg-type]


def test_download_archive_rejects_path_traversal(patch_download) -> None:
    # SECURITY — INTENDED behaviour, currently FAILS. A malicious
    # Content-Disposition escapes the download directory.
    gacetas, set_response = patch_download
    set_response(
        FakeResponse(
            200, {"Content-Disposition": 'attachment; filename="../escape.xlsx"'}, b"PWN"
        )
    )
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert gacetas.resolve() in path.resolve().parents


# ===========================================================================
# download_todays_archive: dedup, filtering, ordering
# ===========================================================================
@pytest.fixture
def patch_archive(monkeypatch, tmp_path):
    """Stub download_archive; record call order, allow per-type results."""
    calls: list[str] = []
    results: dict[str, Path | None] = {}

    async def fake_archive(session, type):
        calls.append(type)
        return results.get(type, tmp_path / f"{type}.bin")

    monkeypatch.setattr(home_download, "download_archive", fake_archive)
    return calls, results


def test_todays_dedups_repeated_types(patch_archive) -> None:
    calls, _ = patch_archive
    saved = run(download_todays_archive("xlsx", "xlsx", "xlsx"))
    assert calls == ["xlsx"]
    assert len(saved) == 1


def test_todays_drops_invalid_types_silently(patch_archive) -> None:
    # Note the asymmetry: download_archive() RAISES on a bad type, but
    # download_todays_archive() filters bad types out before ever calling it.
    calls, _ = patch_archive
    saved = run(download_todays_archive("docx", "txt", "json"))
    assert calls == []
    assert saved == []


def test_todays_mixed_valid_invalid(patch_archive) -> None:
    calls, _ = patch_archive
    saved = run(download_todays_archive("xlsx", "docx"))
    assert calls == ["xlsx"]
    assert len(saved) == 1


def test_todays_both_types(patch_archive) -> None:
    calls, _ = patch_archive
    saved = run(download_todays_archive("xlsx", "pdf"))
    assert set(calls) == {"xlsx", "pdf"}
    assert len(saved) == 2


def test_todays_filters_none_results(patch_archive) -> None:
    calls, results = patch_archive
    results["pdf"] = None
    saved = run(download_todays_archive("xlsx", "pdf"))
    assert len(saved) == 1


def test_todays_no_args_returns_empty(patch_archive) -> None:
    calls, _ = patch_archive
    assert run(download_todays_archive()) == []
    assert calls == []


def test_todays_preserves_caller_order(patch_archive) -> None:
    # INTENDED — currently FLAKY/FAILS. Results should come back in the order
    # the caller asked for, but the implementation iterates a set(), so input
    # order is lost (and varies per process under hash randomization).
    _, results = patch_archive
    results["pdf"] = Path("/p/pdf.bin")
    results["xlsx"] = Path("/p/xlsx.bin")
    saved = run(download_todays_archive("pdf", "xlsx"))
    assert saved == [Path("/p/pdf.bin"), Path("/p/xlsx.bin")]

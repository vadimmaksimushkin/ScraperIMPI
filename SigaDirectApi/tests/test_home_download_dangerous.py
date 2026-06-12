"""Weird & dangerous input for home_download — where the danger is the UNTRUSTED
SERVER RESPONSE, not the caller. The only caller input is `type` ("xlsx"/"pdf"),
fully constrained. Everything dangerous comes back from the (unknown) ASP.NET /
Kestrel server: the status, the body bytes, and the Content-Disposition header
that download_archive turns into a FILENAME WRITTEN TO DISK.

`download_directory / name` with a server-controlled `name` is the core hazard:
  - an absolute path discards download_directory entirely (arbitrary write),
  - "a/b/c.xlsx" / ".." / a null byte / a 300-char name CRASH the write,
  - and the body is saved with no type/size validation, so a 200 error page is
    persisted as an "archive".

The fragile 404 sentinel (exact-bytes `==`) is the other untrusted-response trap:
a benign "no files" body with a stray newline is mis-handled as a hard error.

Section A documents current (dangerous) behaviour and PASSES. Section B asserts
the INTENDED hardening (confine to a safe basename inside DOWNLOAD_PATH; tolerant
sentinel) and currently FAILS/ERRORS. Every write here is confined to tmp_path.
"""

import asyncio
from pathlib import Path

import pytest

import home_download
from home_download import download_archive

NO_FILES = b"No se encontraron archivos PDF para descargar."


class FakeResponse:
    def __init__(self, status: int = 200, headers: dict | None = None, body: bytes = b"") -> None:
        self.status = status
        self.headers = dict(headers or {})
        self._body = body

    async def read(self) -> bytes:
        return self._body


def run(coro):
    return asyncio.run(coro)


def cd_response(filename: str, body: bytes = b"DATA", status: int = 200) -> FakeResponse:
    return FakeResponse(status, {"Content-Disposition": f'attachment; filename="{filename}"'}, body)


@pytest.fixture
def patch_download(monkeypatch, tmp_path):
    """Redirect writes to tmp_path/gacetas and stub the network layer.

    Yields (gacetas_dir, set_response).
    """
    gacetas = tmp_path / "gacetas"
    monkeypatch.setattr(home_download, "DOWNLOAD_PATH", gacetas)

    def set_response(resp: FakeResponse) -> None:
        async def fake_request(**kwargs):
            return resp

        monkeypatch.setattr(home_download, "download_request", fake_request)

    return gacetas, set_response


# ===========================================================================
# Section A — current behaviour, PASSES. The server fully controls the file.
# ===========================================================================
def test_200_error_body_is_saved_not_skipped(patch_download) -> None:
    # The "no files" sentinel only suppresses a 404. The same body with a 200
    # status is happily written to disk as if it were an archive.
    gacetas, set_response = patch_download
    set_response(cd_response("today.xlsx", body=NO_FILES, status=200))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert path.read_bytes() == NO_FILES


def test_server_filename_controls_name_and_extension(patch_download) -> None:
    # An xlsx download whose Content-Disposition says ".exe" is saved as ".exe".
    # Nothing ties the output name/extension to the requested type.
    gacetas, set_response = patch_download
    set_response(cd_response("totally-not-an.exe"))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert path == gacetas / "totally-not-an.exe"


def test_no_magic_byte_validation(patch_download) -> None:
    # A 200 HTML error page is persisted verbatim under an .xlsx name.
    gacetas, set_response = patch_download
    set_response(cd_response("real.xlsx", body=b"<html><body>500</body></html>"))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert path.read_bytes() == b"<html><body>500</body></html>"


# ===========================================================================
# Section B — INTENDED hardening. Currently FAILS / ERRORS.
# ===========================================================================
def test_absolute_path_filename_escapes_download_dir(patch_download, tmp_path) -> None:
    # SECURITY: an absolute Content-Disposition filename discards DOWNLOAD_PATH
    # entirely (pathlib: dir / "/abs" == "/abs"), giving the server an arbitrary
    # absolute write. Confined to tmp_path here, but it lands OUTSIDE gacetas.
    gacetas, set_response = patch_download
    escaped = str(tmp_path / "ESCAPED.xlsx")  # absolute, outside gacetas
    set_response(cd_response(escaped, body=b"PWN"))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert gacetas.resolve() in path.resolve().parents


def test_404_sentinel_with_trailing_newline_means_no_files(patch_download) -> None:
    # The sentinel is matched with exact-bytes ==. A benign "no files" body with
    # a stray newline should still mean "nothing to download" (return None), but
    # the strict compare misses it and it falls through to a RuntimeError.
    gacetas, set_response = patch_download
    set_response(FakeResponse(404, {}, NO_FILES + b"\n"))
    assert run(download_archive(None, "pdf")) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "filename",
    [
        "sub/dir/c.xlsx",      # nonexistent parent -> FileNotFoundError
        "..",                  # the parent directory -> IsADirectoryError
        "ev\x00il.xlsx",       # embedded null byte -> ValueError
        "A" * 300 + ".xlsx",   # over the 255-char filename limit -> OSError
    ],
    ids=["subdir", "dotdot", "null-byte", "overlong"],
)
def test_hostile_filename_should_be_confined_without_crashing(patch_download, filename) -> None:
    # INTENDED: any server filename is reduced to a safe basename and written
    # inside DOWNLOAD_PATH. CURRENTLY each of these crashes the write because the
    # raw name is passed straight to `download_directory / name`.
    gacetas, set_response = patch_download
    set_response(cd_response(filename, body=b"x"))
    path = run(download_archive(None, "xlsx"))  # type: ignore[arg-type]
    assert gacetas.resolve() in path.resolve().parents
    assert path.exists()

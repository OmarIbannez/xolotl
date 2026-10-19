"""Serve the built console from STATIC_DIR with a single-page fallback."""

import logging
import re
from pathlib import Path
from typing import ClassVar, override

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from starlette.convertors import Convertor, register_url_convertor
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

logger = logging.getLogger(__name__)

RESERVED_PREFIXES = ("/v1", "/healthz", "/readyz")
_RESERVED_SEGMENTS = "|".join(re.escape(p.strip("/")) for p in RESERVED_PREFIXES)
_CONVERTOR = "console_path"
# Files the build does not name by content hash must be revalidated on every use,
# or a browser keeps pointing at assets the next release no longer has.
_REVALIDATE = {"Cache-Control": "no-cache"}
# What the browser may do with the console: load its own scripts, styles, fonts, images
# and API calls, and nothing else, and never be framed. style-src keeps 'unsafe-inline'
# because libraries add <style> elements at run time (the toaster when the page loads,
# Radix's modal menus and dialogs when they open); without it the browser blocks them.
# There is no inline script, and the console does not need eval (zod is told not to
# probe for it, see console/src/lib/zod-config.ts), so script-src stays 'self'.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_SECURITY = {
    "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
# The headers of the console's own pages (the index and the fallback). They are not the
# API's: /v1/docs loads its scripts from a CDN, which this policy would block.
_SHELL = {**_REVALIDATE, **_SECURITY}


class _ConsolePath(Convertor[str]):
    """A path that does not start with a reserved prefix, as a whole segment.

    The console's catch-all route is then not a candidate for those paths at all,
    so the router answers them exactly as it would without a console: the API's own
    404 and 405 problems, and no ``index.html`` in place of either.
    """

    regex: ClassVar[str] = rf"(?!(?:{_RESERVED_SEGMENTS})(?:/|$)).*"

    @override
    def convert(self, value: str) -> str:
        return value

    @override
    def to_string(self, value: str) -> str:
        return value


class _Assets(StaticFiles):
    """The build's hashed files, answered with the console's security headers too."""

    @override
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(_SECURITY)
        return response


def _is_file_inside(candidate: Path, root: Path) -> bool:
    """Whether ``candidate`` is a file that, symlinks resolved, lies inside ``root``.

    ``root`` is already resolved. Anything the file system cannot answer is "no": a name
    over 255 bytes or a path over PATH_MAX fails with ENAMETOOLONG, which pathlib on
    CPython 3.13 raises (it only ignores ENOENT, ENOTDIR, EBADF and ELOOP; a NUL it
    already treats as "not a file"). A name that cannot exist is not a file, so the
    request falls through to the index like any other unknown path.

    Returns:
        True only for an existing regular file inside ``root``.

    """
    try:
        return candidate.is_file() and candidate.resolve().is_relative_to(root)
    except OSError:
        return False


def mount_console(app: FastAPI, static_dir: Path) -> bool:
    """Mount the console when ``static_dir/index.html`` exists.

    Call it after every router is included: the catch-all it adds comes last. Both
    outcomes are logged at debug level only: ``build_app`` is also called by tooling
    that prints the OpenAPI document to stdout, so it must not write there by default.

    Returns:
        Whether the console was mounted.

    """
    index = static_dir / "index.html"
    if not index.is_file():
        logger.debug("no console build in %s; serving the API only", static_dir)
        return False
    root = static_dir.resolve()
    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", _Assets(directory=assets), name="console-assets")
    register_url_convertor(_CONVERTOR, _ConsolePath())

    @app.api_route(
        f"/{{full_path:{_CONVERTOR}}}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def console_index(full_path: str) -> Response:
        # lstrip: joining "/etc/passwd" onto a directory would replace the directory.
        candidate = static_dir / full_path.lstrip("/")
        if _is_file_inside(candidate, root):
            return FileResponse(candidate, headers=_SHELL)
        return FileResponse(index, headers=_SHELL)

    logger.debug("serving the console from %s", static_dir)
    return True

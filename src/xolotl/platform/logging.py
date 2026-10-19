"""JSON-lines logging with PII redaction (spec §7).

Values of keys named like a credential or an identifier (``password``,
``token``, ``secret``, ``authorization``, ``api_key``, ``cookie``, ``curp``,
``rfc``, ``clabe``, ``email``; matched case-insensitively with ``-`` read as
``_``, so ``X-Api-Key`` and ``Cookie`` headers are masked) and of any Pydantic
field tagged ``json_schema_extra={"pii": True}`` are replaced by ``***``. That
covers ``extra`` fields, the message itself when it is a mapping or a model, and
message arguments, positional or dict-style. A model is masked by its own tags,
so it needs no registration.

A record whose exception is, or has anywhere in its cause or context chain, a
database error (SQLAlchemy ``DBAPIError`` or ``psycopg.Error``) loses its messages:
the driver text, the statement and its parameters carry row values (``DETAIL: Key
(email)=(...)``), and an outer message may embed them. The filter replaces the
exception with the chain rendered innermost first, each link as its class and its
traceback frames, never ``str(exc)``; a database link also gets the driver class,
SQLSTATE and primary message (dropped for SQLSTATE class 22, whose data exceptions
echo the offending literal). The formatter emits that text as ``exc_info``. Other
exceptions keep their full traceback. This holds for every logger that reaches the
handler. uvicorn logs the exception Starlette re-raises on its own ``uvicorn.error``
logger, whose default configuration has a handler of its own and never reaches ours,
so ``xolotl serve`` runs uvicorn with ``log_config=None``: its loggers then
propagate to the root handler.
"""

import json
import logging
import sys
import traceback
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import cast, override

import psycopg
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError

from xolotl.platform.tenancy.context import current_context

request_id_var: ContextVar[str] = ContextVar("xolotl_request_id", default="")

SENSITIVE_KEYS = frozenset({
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "cookie",
    "curp",
    "rfc",
    "clabe",
    "email",
})
REDACTED = "***"
_PII_FIELDS: set[str] = set()
_STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


def _pii_field_names(model: type[BaseModel]) -> set[str]:
    names: set[str] = set()
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("pii") is True:
            names.add(name)
    return names


def register_pii_model(model: type[BaseModel]) -> None:
    """Mark every field of ``model`` tagged ``{"pii": True}`` as sensitive."""
    _PII_FIELDS.update(_pii_field_names(model))


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in _PII_FIELDS or any(part in lowered for part in SENSITIVE_KEYS)


def _redact_model(model: BaseModel) -> object:
    tagged = _pii_field_names(type(model))
    try:
        # by_alias=False: dump keys must be the field names the tags are stored under.
        dumped = cast(
            "dict[str, object]", model.model_dump(mode="json", by_alias=False)
        )
    except ValueError:  # e.g. a bytes digest that is not UTF-8: mask it, never raise
        return REDACTED
    return {
        key: REDACTED if key in tagged or is_sensitive_key(key) else redact(item)
        for key, item in dumped.items()
    }


def redact(value: object) -> object:
    if isinstance(value, BaseModel):
        return _redact_model(value)
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        return {
            str(k): REDACTED if is_sensitive_key(str(k)) else redact(v)
            for k, v in mapping.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = cast("Iterable[object]", value)
        return [redact(v) for v in items]
    return value


_CAUSE_NOTE = "The above exception was the direct cause of the following exception:"
_CONTEXT_NOTE = "During handling of the above exception, another exception occurred:"


def _database_error_details(error: DBAPIError | psycopg.Error) -> str:
    # The driver's message, the statement and its parameters carry row values (emails,
    # CURPs, "DETAIL: Key (...)=(...)" lines): keep what failed, never the text.
    orig = cast("object", error.orig if isinstance(error, DBAPIError) else error)
    state = cast("object", getattr(orig, "sqlstate", None))
    sqlstate = state if isinstance(state, str) else None
    diag = cast("object", getattr(orig, "diag", None))
    message = cast("object", getattr(diag, "message_primary", None))
    primary = message if isinstance(message, str) else None
    parts = [f"error_type={type(orig).__name__}"]
    if sqlstate:
        parts.append(f"sqlstate={sqlstate}")
    # Data exceptions (SQLSTATE class 22) echo the offending literal in the primary.
    if primary and not (sqlstate or "").startswith("22"):
        parts.append(f"primary={primary!r}")
    return " ".join(parts)


def _link_text(error: BaseException, tb: TracebackType | None) -> str:
    # Class and frames only: str(error) is, or may embed, a database message.
    head = type(error).__name__
    if isinstance(error, DBAPIError | psycopg.Error):
        head = f"{head} {_database_error_details(error)}"
    frames = "".join(traceback.format_tb(tb)).rstrip("\n")
    return f"{head}\nframes:\n{frames}"


def _exception_chain(error: BaseException) -> list[tuple[BaseException, str]]:
    # Outermost first, each paired with the note that joins it to the exception raised
    # after it. Follows the links traceback prints (the cause, else a context that is
    # not suppressed); the seen-set stops cycles.
    chain: list[tuple[BaseException, str]] = []
    seen: set[int] = set()
    link: BaseException | None = error
    note = ""
    while link is not None and id(link) not in seen:
        seen.add(id(link))
        chain.append((link, note))
        if link.__cause__ is not None:
            link, note = link.__cause__, _CAUSE_NOTE
        elif link.__suppress_context__:
            link = None
        else:
            link, note = link.__context__, _CONTEXT_NOTE
    return chain


def _chain_text(
    chain: list[tuple[BaseException, str]], tb: TracebackType | None
) -> str:
    # Innermost first, like traceback; the logged exception keeps the record's tb.
    blocks: list[str] = []
    for position, (link, note) in reversed(list(enumerate(chain))):
        blocks.append(_link_text(link, tb if position == 0 else link.__traceback__))
        if position:
            blocks.append(note)
    return "\n\n".join(blocks)


def _scrub_database_error(record: logging.LogRecord) -> None:
    exc_info = record.exc_info
    if not exc_info:  # None, or False when a caller passed exc_info=False
        return
    error = exc_info[1]
    if error is None:
        return
    chain = _exception_chain(error)
    if any(isinstance(link, DBAPIError | psycopg.Error) for link, _ in chain):
        record.exc_text = _chain_text(chain, exc_info[2])
        record.exc_info = None  # the formatter must not print the original


class RedactionFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        message = cast("object", record.msg)
        if isinstance(message, Mapping | BaseModel):
            # isinstance narrows to Mapping[Unknown, object]; redact takes any object.
            record.msg = redact(cast("object", message))
        if isinstance(record.args, Mapping):
            record.args = cast("Mapping[str, object]", redact(record.args))
        elif isinstance(record.args, tuple):
            record.args = tuple(redact(arg) for arg in record.args)
        _scrub_database_error(record)
        for key in list(vars(record)):
            if key in _STANDARD_ATTRS:
                continue
            value = cast("object", getattr(record, key))
            setattr(record, key, REDACTED if is_sensitive_key(key) else redact(value))
        return True


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if request_id := request_id_var.get():
            payload["request_id"] = request_id
        ctx = current_context.get()
        if ctx is not None and ctx.tenant_id is not None:
            payload["tenant_id"] = str(ctx.tenant_id)
        fields = cast("dict[str, object]", vars(record))
        payload.update({
            key: value for key, value in fields.items() if key not in _STANDARD_ATTRS
        })
        if record.exc_text:  # set by RedactionFilter when it scrubbed the exception
            payload["exc_info"] = record.exc_text
        elif record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn's access log duplicates the request line emitted by RequestIdMiddleware.
    logging.getLogger("uvicorn.access").disabled = True

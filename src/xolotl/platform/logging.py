"""JSON-lines logging with PII redaction (spec §7).

Values of keys named like a credential or an identifier (``password``,
``token``, ``secret``, ``authorization``, ``api_key``, ``master_key``, ``cookie``,
``curp``, ``rfc``, ``clabe``, ``email``; matched case-insensitively with ``-`` read as
``_``, so ``X-Api-Key`` and ``Cookie`` headers are masked) and of any Pydantic
field tagged ``json_schema_extra={"pii": True}`` are replaced by ``***``. That
covers ``extra`` fields, the message itself when it is a mapping or a model, and
message arguments, positional or dict-style. A model is masked by its own tags,
so it needs no registration. An object whose class sets ``log_opaque = True``
(``Encrypted``) is masked whole, wherever it appears.

A record whose exception is, or has anywhere in its cause or context chain, a
database error (any SQLAlchemy error, or ``psycopg.Error``) loses its messages:
the driver text, the statement and its parameters carry row values (``DETAIL: Key
(email)=(...)``), and an outer message may embed them. That includes a SQLAlchemy
error that is not the driver's, such as the ``PendingRollbackError`` a session raises
after a failed flush, which quotes the original error in its own message and has none
in its chain. The filter replaces the exception with the chain rendered innermost
first, each link as its class and its traceback frames, never ``str(exc)``; a driver
error link also gets the driver class, SQLSTATE and primary message (dropped for
SQLSTATE class 22, whose data exceptions echo the offending literal). The formatter
emits that text as ``exc_info``. Other exceptions keep their full traceback. This
holds for every logger that reaches the handler. uvicorn logs the exception Starlette
re-raises on its own ``uvicorn.error`` logger, whose default configuration has a
handler of its own and never reaches ours, so ``xolotl serve`` runs uvicorn with
``log_config=None``: its loggers then propagate to the root handler.

Text that is stored instead of logged never meets the filter. ``job.last_error`` is
the example: it is a plain column that outlives the log line and sits outside the
audit trail. ``safe_exception_summary`` applies the same rule at the moment the text
is written, to a stricter degree because there are no frames to keep: any SQLAlchemy
or psycopg error reachable through ``__cause__`` or ``__context__`` reduces the whole
exception to its class and SQLSTATE, a Pydantic ``ValidationError`` (which echoes the
input) to its error count, and only an exception with neither keeps its message. Its
walk is wider than the filter's: it also follows a context that ``raise ... from
None`` hides from tracebacks, because the message may have quoted it all the same.
The text it returns can always be written: producing it cannot raise, and it holds
no NUL and no lone surrogate.

At DEBUG SQLAlchemy prints every result row of ``sqlalchemy.engine``, which would put
password hashes, e-mail addresses, wrapped keys and ciphertexts in the log, and the
filter cannot see inside a ``Row``. Two things keep it out. ``configure_logging``
raises that logger to INFO when it would be more verbose (its default, WARNING, is
left alone, so the cap never turns statement logging on), which also saves formatting
rows that would be dropped. And the filter drops any record of that logger or a child
of it below INFO, so the rows stay out of the handler even when the logger is lifted
again later (a ``setLevel(DEBUG)``) or an engine is created with ``echo="debug"``.
What it cannot reach is the stream handler SQLAlchemy attaches itself to the engine
logger for an ``echo`` engine: the application creates none.

The HTTP client is treated the same way. ``httpx`` logs each request it makes, method
and full URL, at INFO, and ``httpcore`` logs hosts and connection steps at DEBUG. The
URL of a webhook, like that of any service we call, often carries a token in its path
or query, and a message is beyond the redaction filter. ``configure_logging`` therefore
holds both loggers at WARNING, and the filter drops their records below WARNING.
"""

import json
import logging
import sys
import traceback
from collections import deque
from collections.abc import Iterable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import cast, override

import psycopg
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from xolotl.platform.tenancy.context import current_context

request_id_var: ContextVar[str] = ContextVar("xolotl_request_id", default="")

# The longest exception summary safe_exception_summary returns.
EXCEPTION_SUMMARY_LIMIT = 500

SENSITIVE_KEYS = frozenset({
    "password",
    "token",
    "secret",
    "authorization",
    "api_key",
    "master_key",
    "cookie",
    "curp",
    "rfc",
    "clabe",
    "email",
})
REDACTED = "***"
ENGINE_LOGGER = "sqlalchemy.engine"
# The HTTP client's loggers: they log the URL of every request (see the module doc).
OUTBOUND_HTTP_LOGGERS = ("httpx", "httpcore")
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


def _is_opaque(value: object) -> bool:
    # A class declares itself opaque (``Encrypted`` does) instead of being recognised
    # by import: logging has to stay importable without the crypto package.
    return getattr(type(value), "log_opaque", False) is True


def redact(value: object) -> object:
    if _is_opaque(value):
        return REDACTED
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


def _driver_error(error: BaseException) -> object:
    # SQLAlchemy keeps the driver's own error in .orig; a psycopg error is its own.
    return cast("object", error.orig if isinstance(error, DBAPIError) else error)


def _sqlstate(error: BaseException) -> str | None:
    state = cast("object", getattr(_driver_error(error), "sqlstate", None))
    return state if isinstance(state, str) else None


def _database_error_details(error: DBAPIError | psycopg.Error) -> str:
    # The driver's message, the statement and its parameters carry row values (emails,
    # CURPs, "DETAIL: Key (...)=(...)" lines): keep what failed, never the text.
    orig = _driver_error(error)
    sqlstate = _sqlstate(error)
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
    if any(isinstance(link, SQLAlchemyError | psycopg.Error) for link, _ in chain):
        record.exc_text = _chain_text(chain, exc_info[2])
        record.exc_info = None  # the formatter must not print the original


def _reachable(error: BaseException) -> list[BaseException]:
    # Every exception reachable from ``error`` through __cause__ or __context__, the
    # outermost first. Unlike _exception_chain, which prints what traceback prints, it
    # does not stop at a context that ``raise ... from None`` suppresses: suppressing
    # only hides it from tracebacks, and the new message may have quoted it.
    found: list[BaseException] = []
    seen: set[int] = set()
    pending = deque([error])
    while pending:
        link = pending.popleft()
        if id(link) in seen:
            continue
        seen.add(id(link))
        found.append(link)
        pending.extend(
            nested
            for nested in (link.__cause__, link.__context__)
            if nested is not None
        )
    return found


def _storable(text: str) -> str:
    # Postgres text cannot hold a NUL, and psycopg cannot encode a lone surrogate
    # (which str allows): either one would make the write that records a failure fail.
    return text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _summary(error: BaseException) -> str:
    related = _reachable(error)
    name = type(error).__name__
    if any(isinstance(link, SQLAlchemyError | psycopg.Error) for link in related):
        states = (_sqlstate(link) for link in related)
        sqlstate = next((state for state in states if state), None)
        return f"{name} [sqlstate={sqlstate}]" if sqlstate else name
    invalid = next(
        (link for link in related if isinstance(link, ValidationError)), None
    )
    if invalid is not None:
        count = invalid.error_count()
        if invalid is error:
            return f"{name} ({count} errors)"
        return f"{name} (caused by ValidationError, {count} errors)"
    message = f"<unprintable {name}>"
    with suppress(Exception):  # an exception whose __str__ raises must not fail us too
        message = str(error)
    return f"{name}: {message}" if message else name


def safe_exception_summary(error: BaseException) -> str:
    """Summarise ``error`` in one short line that is safe to store.

    For text that goes into a table rather than a log (``job.last_error``): the
    redaction filter never sees it, so the message must not carry row values. Any
    SQLAlchemy or psycopg error reachable through ``__cause__`` or ``__context__``,
    suppressed or not, makes the whole exception sensitive, however it is wrapped: the
    driver text echoes the values it choked on, an outer message may embed it
    (``raise Domain(f"... {exc}") from exc``, or ``from None``, which hides the
    context from tracebacks but leaves it in place), and a session error such as
    ``PendingRollbackError`` quotes the original error. A Pydantic
    ``ValidationError`` echoes the input value of every failing field and is
    summarised the same way.

    The text can always be stored: it is built without raising, even when the
    exception's ``__str__`` does (the message is then ``<unprintable Class>``), and it
    holds no NUL and no lone surrogate, which a Postgres text column cannot take.

    Returns:
        ``"<Class> [sqlstate=XXXXX]"`` for a database error (the class alone when
        the driver gave no SQLSTATE), ``"ValidationError (N errors)"`` for a
        validation error, ``"<Class> (caused by ValidationError, N errors)"`` when one
        is further down, and otherwise ``"<Class>: <message>"``, all cut to
        ``EXCEPTION_SUMMARY_LIMIT`` characters. ``<Class>`` is the outermost
        exception's.

    """
    return _storable(_summary(error))[:EXCEPTION_SUMMARY_LIMIT]


def _is_engine_row_log(record: logging.LogRecord) -> bool:
    in_engine_tree = record.name == ENGINE_LOGGER or record.name.startswith(
        f"{ENGINE_LOGGER}."
    )
    return in_engine_tree and record.levelno < logging.INFO


def _is_http_client_log(record: logging.LogRecord) -> bool:
    in_client_tree = any(
        record.name == name or record.name.startswith(f"{name}.")
        for name in OUTBOUND_HTTP_LOGGERS
    )
    return in_client_tree and record.levelno < logging.WARNING


class RedactionFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if _is_engine_row_log(record) or _is_http_client_log(record):
            return False
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
    engine_logger = logging.getLogger(ENGINE_LOGGER)
    if engine_logger.getEffectiveLevel() < logging.INFO:
        engine_logger.setLevel(logging.INFO)
    for name in OUTBOUND_HTTP_LOGGERS:
        client_logger = logging.getLogger(name)
        if client_logger.getEffectiveLevel() < logging.WARNING:
            client_logger.setLevel(logging.WARNING)
    # uvicorn's access log duplicates the request line emitted by RequestIdMiddleware.
    logging.getLogger("uvicorn.access").disabled = True

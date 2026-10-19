import io
import json
import logging
from types import SimpleNamespace
from typing import override
from uuid import uuid4

import httpx
import pytest
import respx
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import DataError, IntegrityError, PendingRollbackError

from tests.helpers import make_tenant, system_ctx
from xolotl.modules.tenants.models import Tenant
from xolotl.platform.crypto.encrypted import STR_CODEC, Encrypted
from xolotl.platform.db import get_engine, tenant_session
from xolotl.platform.enums import KeyPurpose
from xolotl.platform.logging import (
    EXCEPTION_SUMMARY_LIMIT,
    JsonFormatter,
    RedactionFilter,
    configure_logging,
    is_sensitive_key,
    redact,
    register_pii_model,
    request_id_var,
    safe_exception_summary,
)
from xolotl.platform.tenancy.context import bind_context

# Shaped like psycopg's: a primary line, then a DETAIL line that echoes row values.
DRIVER_MESSAGE = """duplicate key value violates unique constraint
DETAIL: Key (email)=(a@b.mx) already exists."""


def _capture() -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger(f"test.{uuid4().hex}")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger, stream


def _last_line(stream: io.StringIO) -> dict:
    return json.loads(stream.getvalue().strip().splitlines()[-1])


class Subject(BaseModel, frozen=True):
    """Never passed to register_pii_model: the masking comes from its own tag."""

    maiden_name: str = Field(json_schema_extra={"pii": True})
    city: str


MASKED_SUBJECT = {"maiden_name": "***", "city": "CDMX"}


def test_sensitive_key_matching():
    assert is_sensitive_key("password")
    assert is_sensitive_key("Access_Token")
    assert is_sensitive_key("AUTHORIZATION")
    assert is_sensitive_key("customer_rfc")
    assert is_sensitive_key("email")
    assert not is_sensitive_key("status")


def test_redact_walks_nested_structures():
    assert redact({
        "email": "a@b.mx",
        "n": {"token": "t", "ok": 1},
        "l": [{"curp": "x"}],
    }) == {
        "email": "***",
        "n": {"token": "***", "ok": 1},
        "l": [{"curp": "***"}],
    }


def test_extra_fields_and_dict_args_are_redacted():
    logger, stream = _capture()
    logger.info(
        "login for %(email)s",
        {"email": "a@b.mx"},
        extra={"password": "hunter2", "detail": {"secret": "s", "count": 2}},
    )
    line = _last_line(stream)
    assert line["msg"] == "login for ***"
    assert line["password"] == "***"
    assert line["detail"] == {"secret": "***", "count": 2}
    assert line["level"] == "INFO"


def test_pii_tagged_pydantic_fields_are_redacted_after_registration():
    class Person(BaseModel):
        given_name: str = Field(json_schema_extra={"pii": True})
        city: str

    logger, stream = _capture()
    logger.info("before", extra={"given_name": "Ana", "city": "CDMX"})
    assert _last_line(stream)["given_name"] == "Ana"
    register_pii_model(Person)
    logger.info("after", extra={"given_name": "Ana", "city": "CDMX"})
    line = _last_line(stream)
    assert (line["given_name"], line["city"]) == ("***", "CDMX")


def test_request_id_and_tenant_id_are_included_when_bound():
    logger, stream = _capture()
    tenant_id = uuid4()
    token = request_id_var.set("req-123")
    try:
        with bind_context(system_ctx(tenant_id)):
            logger.info("hello")
    finally:
        request_id_var.reset(token)
    line = _last_line(stream)
    assert line["request_id"] == "req-123"
    assert line["tenant_id"] == str(tenant_id)
    logger.info("outside")
    assert "request_id" not in _last_line(stream)


def test_exceptions_are_serialised():
    logger, stream = _capture()
    try:
        _ = 1 / int("0")  # a literal 1 / 0 is rejected by ty's division-by-zero rule
    except ZeroDivisionError:
        logger.exception("failed")
    assert "ZeroDivisionError" in _last_line(stream)["exc_info"]


def test_positional_args_are_redacted():
    logger, stream = _capture()
    logger.info("u %s p %s", "u1", {"email": "a@b.mx"})
    assert _last_line(stream)["msg"] == "u u1 p {'email': '***'}"


def test_filter_keeps_positional_args_a_tuple():
    args = ("u1", {"email": "a@b.mx"})
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "%s %s", args, None)
    assert RedactionFilter().filter(record)
    assert isinstance(record.args, tuple)
    assert record.args == ("u1", {"email": "***"})


def test_model_arguments_are_masked_by_their_own_tags_without_registration():
    logger, stream = _capture()
    logger.info("created %s", Subject(maiden_name="Lopez", city="CDMX"))
    assert _last_line(stream)["msg"] == f"created {MASKED_SUBJECT}"


def test_model_extras_are_masked_by_their_own_tags_without_registration():
    logger, stream = _capture()
    logger.info("created", extra={"subject": Subject(maiden_name="Lopez", city="CDMX")})
    assert _last_line(stream)["subject"] == MASKED_SUBJECT


def test_mapping_message_is_redacted():
    logger, stream = _capture()
    logger.info({"email": "a@b.mx", "ok": 1})
    assert _last_line(stream)["msg"] == "{'email': '***', 'ok': 1}"


def test_model_message_is_redacted():
    logger, stream = _capture()
    logger.info(Subject(maiden_name="Lopez", city="CDMX"))
    assert _last_line(stream)["msg"] == str(MASKED_SUBJECT)


@pytest.mark.parametrize("container", [set, frozenset])
def test_sets_are_walked_like_lists(container):
    logger, stream = _capture()
    people = container({Subject(maiden_name="Lopez", city="CDMX")})
    logger.info("batch", extra={"people": people})
    assert _last_line(stream)["people"] == [MASKED_SUBJECT]


def test_models_serialising_by_alias_are_still_masked_by_field_name():
    class Aliased(BaseModel):
        model_config = ConfigDict(serialize_by_alias=True)

        maiden_name: str = Field(alias="MaidenName", json_schema_extra={"pii": True})
        city: str

    subject = Aliased.model_validate({"MaidenName": "Lopez", "city": "CDMX"})
    logger, stream = _capture()
    logger.info("created %s", subject)
    assert _last_line(stream)["msg"] == f"created {MASKED_SUBJECT}"


def test_models_that_cannot_be_dumped_are_masked_not_raised():
    class Blob(BaseModel):
        digest: bytes

    logger, stream = _capture()
    logger.info("hash %s", Blob(digest=bytes.fromhex("ff00fe")))
    assert _last_line(stream)["msg"] == "hash ***"


def test_header_keys_are_masked_whatever_their_case_or_hyphens():
    assert is_sensitive_key("api-key")
    assert is_sensitive_key("X-Api-Key")
    assert is_sensitive_key("Cookie")
    headers = {
        "X-Api-Key": "k1",
        "api-key": "k2",
        "Cookie": "sid=1",
        "Set-Cookie": "sid=2",
        "Authorization": "Bearer t",
        "Accept": "text/html",
    }
    logger, stream = _capture()
    logger.info("request", extra={"headers": headers})
    assert _last_line(stream)["headers"] == {
        "X-Api-Key": "***",
        "api-key": "***",
        "Cookie": "***",
        "Set-Cookie": "***",
        "Authorization": "***",
        "Accept": "text/html",
    }


def _raise(error: Exception) -> None:
    raise error


@pytest.fixture
def served_logging():
    # Root set up by configure_logging; uvicorn.error has no handler of its own and
    # propagates to it, which is how the process logs once `xolotl serve` runs
    # uvicorn with log_config=None. Yields the root handler's stream.
    root = logging.getLogger()
    uvicorn_error = logging.getLogger("uvicorn.error")
    handlers, level = list(root.handlers), root.level
    own_handlers, propagate = list(uvicorn_error.handlers), uvicorn_error.propagate
    configure_logging("INFO")
    (handler,) = root.handlers
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    _ = handler.setStream(stream)
    uvicorn_error.handlers.clear()
    uvicorn_error.propagate = True
    yield stream
    root.handlers[:] = handlers
    root.setLevel(level)
    uvicorn_error.handlers[:] = own_handlers
    uvicorn_error.propagate = propagate


@pytest.mark.parametrize(
    ("kind", "head"),
    [
        ("sqlalchemy", "IntegrityError error_type=Exception"),
        ("psycopg", "UniqueViolation error_type=UniqueViolation sqlstate=23505"),
    ],
)
def test_database_errors_are_scrubbed_whichever_logger_emits_them(
    served_logging, kind, head
):
    if kind == "sqlalchemy":
        statement = "INSERT INTO person (email) VALUES (%(email)s)"
        params = {"email": "a@b.mx"}
        error = IntegrityError(statement, params, Exception(DRIVER_MESSAGE))
    else:
        error = UniqueViolation(DRIVER_MESSAGE)
    try:
        _raise(error)
    except (IntegrityError, UniqueViolation):
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    text = served_logging.getvalue()
    assert "a@b.mx" not in text
    assert "duplicate key" not in text
    (line,) = [json.loads(row) for row in text.splitlines()]
    assert line["logger"] == "uvicorn.error"
    assert line["msg"] == "Exception in ASGI application"
    assert line["exc_info"].startswith(head)
    assert "frames:" in line["exc_info"]
    assert "_raise" in line["exc_info"]


def test_a_session_error_that_quotes_the_database_message_is_scrubbed_from_the_log(
    served_logging,
):
    # After a failed flush the session refuses further work and quotes the original
    # error, row values included, in its own message. It is not a driver error and no
    # driver error is in its chain, but it is a SQLAlchemy error all the same.
    original = f"(psycopg.errors.UniqueViolation) {DRIVER_MESSAGE}"
    refusal = "This Session's transaction has been rolled back"
    try:
        _raise(PendingRollbackError(f"{refusal}. Original exception was: {original}"))
    except PendingRollbackError:
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    text = served_logging.getvalue()
    assert "a@b.mx" not in text
    assert "duplicate key" not in text
    (line,) = [json.loads(row) for row in text.splitlines()]
    assert line["exc_info"].startswith("PendingRollbackError")
    assert "frames:" in line["exc_info"]
    assert "_raise" in line["exc_info"]


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str, primary: str) -> None:
        super().__init__(f"driver text that must never be logged: {primary}")
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(message_primary=primary)


@pytest.mark.parametrize(
    ("sqlstate", "primary", "logged"),
    [
        ("22P02", 'invalid input syntax for type uuid: "a@b.mx"', False),
        ("23505", 'duplicate key value violates unique constraint "uq_slug"', True),
    ],
)
def test_primary_message_is_dropped_for_data_exceptions_only(sqlstate, primary, logged):
    logger, stream = _capture()
    try:
        _raise(DataError("SELECT 1", None, FakeDriverError(sqlstate, primary)))
    except DataError:
        logger.exception("failed")
    assert "a@b.mx" not in stream.getvalue()
    scrubbed = _last_line(stream)["exc_info"]
    head = f"DataError error_type=FakeDriverError sqlstate={sqlstate}"
    assert scrubbed.startswith(head)
    assert (f"primary={primary!r}" in scrubbed) is logged


def _save() -> None:
    statement = "INSERT INTO person (email) VALUES (%(email)s)"
    params = {"email": "a@b.mx"}
    raise IntegrityError(statement, params, Exception(DRIVER_MESSAGE))


def _fail() -> None:
    msg = "second failure"
    raise RuntimeError(msg)


def _wrap_with_cause() -> None:
    try:
        _save()
    except IntegrityError as exc:
        # Application code often embeds the inner error in the outer message.
        msg = f"while saving ana: {exc}"
        raise RuntimeError(msg) from exc


def _wrap_with_context() -> None:
    try:
        _save()
    except IntegrityError:
        _fail()  # raised while handling the IntegrityError: implicit __context__


def _wrap_hiding_the_context() -> None:
    try:
        _save()
    except IntegrityError:
        msg = "second failure"
        raise RuntimeError(msg) from None


def _wrap_without_database_error() -> None:
    try:
        _raise(ValueError("inner detail"))
    except ValueError as exc:
        msg = "outer detail"
        raise RuntimeError(msg) from exc


def test_database_errors_are_scrubbed_anywhere_in_an_exception_chain():
    logger, stream = _capture()
    try:
        _wrap_with_cause()
    except RuntimeError:
        logger.exception("failed")
    assert "a@b.mx" not in stream.getvalue()
    # The outer message embeds the inner one and is never printed either.
    assert "while saving ana" not in stream.getvalue()
    scrubbed = _last_line(stream)["exc_info"]
    assert scrubbed.index("IntegrityError error_type=Exception") < scrubbed.index(
        "RuntimeError"
    )
    assert "The above exception was the direct cause of the following exception:" in (
        scrubbed
    )
    assert "_save" in scrubbed
    assert "_wrap_with_cause" in scrubbed


def test_database_errors_reached_through_the_context_are_scrubbed_too():
    logger, stream = _capture()
    try:
        _wrap_with_context()
    except RuntimeError:
        logger.exception("failed")
    assert "a@b.mx" not in stream.getvalue()
    assert "second failure" not in stream.getvalue()
    scrubbed = _last_line(stream)["exc_info"]
    assert "During handling of the above exception, another exception occurred:" in (
        scrubbed
    )
    assert "IntegrityError" in scrubbed
    assert "RuntimeError" in scrubbed


def test_exception_cycles_are_followed_once():
    logger, stream = _capture()
    inner = IntegrityError("INSERT", {"email": "a@b.mx"}, Exception(DRIVER_MESSAGE))
    outer = RuntimeError("wraps a@b.mx")
    outer.__cause__ = inner
    inner.__cause__ = outer
    logger.error("failed", exc_info=outer)
    assert "a@b.mx" not in stream.getvalue()
    scrubbed = _last_line(stream)["exc_info"]
    assert scrubbed.count("IntegrityError") == 1
    assert scrubbed.count("RuntimeError") == 1


def test_chains_without_a_database_error_keep_their_full_traceback():
    logger, stream = _capture()
    try:
        _wrap_without_database_error()
    except RuntimeError:
        logger.exception("failed")
    traced = _last_line(stream)["exc_info"]
    assert "Traceback (most recent call last)" in traced
    assert "inner detail" in traced
    assert "outer detail" in traced


def test_exc_info_without_an_active_exception_is_left_alone():
    logger, stream = _capture()
    # What exc_info=True records when nothing is being handled.
    logger.error("failed", exc_info=(None, None, None))
    assert _last_line(stream)["exc_info"] == "NoneType: None"


def test_a_database_error_that_traceback_would_not_print_keeps_the_full_traceback():
    logger, stream = _capture()
    try:
        _wrap_hiding_the_context()  # raise ... from None suppresses the context
    except RuntimeError:
        logger.exception("failed")
    traced = _last_line(stream)["exc_info"]
    assert "Traceback (most recent call last)" in traced
    assert "second failure" in traced
    assert "a@b.mx" not in traced


# --- safe_exception_summary: exception text that is stored, not logged ----------------


class Applicant(BaseModel):
    curp: str = Field(max_length=5)
    age: int


def _invalid_applicant(**values) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        _ = Applicant(**values)
    return caught.value


def _unique_violation_in_sqlalchemy() -> IntegrityError:
    statement = "INSERT INTO person (email) VALUES (%(email)s)"
    return IntegrityError(
        statement, {"email": "a@b.mx"}, UniqueViolation(DRIVER_MESSAGE)
    )


def test_a_plain_exception_keeps_its_class_and_message():
    summary = safe_exception_summary(ValueError("quantity must be positive"))
    assert summary == "ValueError: quantity must be positive"


def test_an_exception_without_a_message_is_just_its_class():
    assert safe_exception_summary(AssertionError()) == "AssertionError"


def test_a_long_message_is_cut_to_the_limit():
    summary = safe_exception_summary(RuntimeError("x" * 10 * EXCEPTION_SUMMARY_LIMIT))
    assert EXCEPTION_SUMMARY_LIMIT == 500
    assert len(summary) == EXCEPTION_SUMMARY_LIMIT
    assert summary.startswith("RuntimeError: xxx")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("raw psycopg", "UniqueViolation [sqlstate=23505]"),
        ("wrapped psycopg", "IntegrityError [sqlstate=23505]"),
        ("wrapped driver", "DataError [sqlstate=22P02]"),
        ("no sqlstate", "IntegrityError"),
    ],
)
def test_a_database_error_keeps_only_its_class_and_sqlstate(kind, expected):
    errors = {
        "raw psycopg": UniqueViolation(DRIVER_MESSAGE),
        "wrapped psycopg": _unique_violation_in_sqlalchemy(),
        "wrapped driver": DataError(
            "SELECT 1",
            {"email": "a@b.mx"},
            FakeDriverError("22P02", 'invalid input syntax for type uuid: "a@b.mx"'),
        ),
        "no sqlstate": IntegrityError(
            "INSERT", {"email": "a@b.mx"}, Exception(DRIVER_MESSAGE)
        ),
    }
    summary = safe_exception_summary(errors[kind])
    assert summary == expected
    assert "a@b.mx" not in summary


@pytest.mark.parametrize("link", ["cause", "context"])
def test_an_exception_that_embeds_a_database_message_is_summarised_without_it(link):
    # The ordinary `except ...: raise Domain(f"... {exc}") from exc`.
    inner = _unique_violation_in_sqlalchemy()
    outer = RuntimeError(f"party load failed: {inner}")
    if link == "cause":
        outer.__cause__ = inner
    else:
        outer.__context__ = inner
    assert "a@b.mx" in str(outer)  # the message does carry the value
    summary = safe_exception_summary(outer)
    assert summary == "RuntimeError [sqlstate=23505]"


def test_a_cyclic_exception_chain_is_summarised_once():
    inner = _unique_violation_in_sqlalchemy()
    outer = RuntimeError("wraps a@b.mx")
    outer.__cause__ = inner
    inner.__cause__ = outer
    assert safe_exception_summary(outer) == "RuntimeError [sqlstate=23505]"


def test_a_session_error_that_quotes_the_database_message_is_summarised_without_it():
    # The session refuses further work after a failed flush and quotes the original
    # error, row values included, in its own message.
    original = f"(psycopg.errors.UniqueViolation) {DRIVER_MESSAGE}"
    refusal = "This Session's transaction has been rolled back"
    error = PendingRollbackError(f"{refusal}. Original exception was: {original}")
    assert "a@b.mx" in str(error)
    assert safe_exception_summary(error) == "PendingRollbackError"


def test_a_validation_error_is_summarised_by_its_error_count():
    invalid = _invalid_applicant(curp="ABCD010101HDFXXX01", age="not a number")
    assert "ABCD010101HDFXXX01" in str(invalid)  # pydantic echoes the input
    summary = safe_exception_summary(invalid)
    assert summary == "ValidationError (2 errors)"


def test_an_exception_that_embeds_a_validation_error_is_summarised_without_it():
    invalid = _invalid_applicant(curp="ABCD010101HDFXXX01", age=7)
    outer = RuntimeError(f"bad applicant: {invalid}")
    outer.__cause__ = invalid
    summary = safe_exception_summary(outer)
    assert summary == "RuntimeError (caused by ValidationError, 1 errors)"
    assert "ABCD010101HDFXXX01" not in summary


def test_a_context_hidden_by_raise_from_none_is_still_searched_for_a_database_error():
    # `raise Domain(f"... {exc}") from None` hides the context from tracebacks (and so
    # from the log filter, which prints what traceback prints), but the exception is
    # still __context__ and the message may have quoted it.
    inner = _unique_violation_in_sqlalchemy()
    outer = RuntimeError(f"party load failed: {inner}")
    outer.__context__ = inner
    outer.__suppress_context__ = True
    assert "a@b.mx" in str(outer)
    assert safe_exception_summary(outer) == "RuntimeError [sqlstate=23505]"


def test_a_context_cycle_is_followed_once():
    inner = _unique_violation_in_sqlalchemy()
    outer = RuntimeError("wraps a@b.mx")
    outer.__context__ = inner
    inner.__context__ = outer
    assert safe_exception_summary(outer) == "RuntimeError [sqlstate=23505]"


def test_the_filter_still_prints_what_traceback_prints_for_a_suppressed_context():
    # The summary looks further than traceback does; the log filter must not change.
    logger, stream = _capture()
    try:
        _wrap_hiding_the_context()
    except RuntimeError:
        logger.exception("failed")
    traced = _last_line(stream)["exc_info"]
    assert "Traceback (most recent call last)" in traced
    assert "IntegrityError" not in traced


def test_an_exception_whose_str_raises_is_summarised_by_its_class():
    class OddError(Exception):
        @override
        def __str__(self) -> str:
            msg = "self.response is None"
            raise AttributeError(msg)

    assert safe_exception_summary(OddError()) == "OddError: <unprintable OddError>"


def test_nul_characters_and_lone_surrogates_are_removed_from_the_summary():
    summary = safe_exception_summary(ValueError("a\x00b \ud800 c\udfff"))
    assert summary == "ValueError: ab ? c?"
    _ = summary.encode("utf-8")  # a lone surrogate would raise here
    assert "\x00" not in summary


def test_the_summary_is_cleaned_before_it_is_cut():
    # Cutting first would leave fewer characters than the limit once the NULs go.
    summary = safe_exception_summary(RuntimeError("\x00" * 50 + "x" * 1000))
    assert len(summary) == EXCEPTION_SUMMARY_LIMIT
    assert summary.startswith("RuntimeError: xxx")


# ---- key material and SQL row logging (plan 1b task B3) ---------------------------


@pytest.mark.parametrize(
    "key",
    [
        "master_key",
        "MASTER_KEY",
        "master-key",
        "Xolotl-Master-Key",
        "XOLOTL_MASTER_KEY",
    ],
)
def test_master_key_is_a_sensitive_key(key):
    assert is_sensitive_key(key)


def test_a_master_key_in_extra_fields_or_nested_mappings_is_masked():
    logger, stream = _capture()
    logger.info(
        "configured",
        extra={"master_key": "a2V5", "settings": {"master-key": "a2V5", "port": 8000}},
    )
    line = _last_line(stream)
    assert line["master_key"] == "***"
    assert line["settings"] == {"master-key": "***", "port": 8000}
    assert "a2V5" not in stream.getvalue()


ENGINE_LOGGER = "sqlalchemy.engine"


OUTBOUND_LOGGERS = ("httpx", "httpcore")


@pytest.fixture
def untouched_logging():
    # configure_logging replaces the root handlers and sets levels process-wide.
    names = ("", "sqlalchemy", ENGINE_LOGGER, *OUTBOUND_LOGGERS)
    loggers = [logging.getLogger(name) for name in names]
    handlers, levels = list(loggers[0].handlers), [logger.level for logger in loggers]
    yield
    loggers[0].handlers[:] = handlers
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


@pytest.mark.parametrize(
    ("parent", "engine", "configured", "effective"),
    [
        # Lowered by someone (sitecustomize, a logging config, echo recipes): capped.
        (logging.WARNING, logging.DEBUG, "DEBUG", logging.INFO),
        (logging.WARNING, logging.DEBUG, "WARNING", logging.INFO),
        # A parent that follows the root level would otherwise pass DEBUG down.
        (logging.NOTSET, logging.NOTSET, "DEBUG", logging.INFO),
        # INFO is allowed, and nothing quieter is raised: capping never turns
        # statement logging on (SQLAlchemy's own default is WARNING).
        (logging.WARNING, logging.INFO, "DEBUG", logging.INFO),
        (logging.WARNING, logging.NOTSET, "DEBUG", logging.WARNING),
        (logging.WARNING, logging.NOTSET, "INFO", logging.WARNING),
        (logging.WARNING, logging.ERROR, "DEBUG", logging.ERROR),
    ],
)
def test_the_engine_logger_is_never_more_verbose_than_info(
    untouched_logging, parent, engine, configured, effective
):
    logging.getLogger().setLevel(logging.WARNING)  # quieter than anything configured
    logging.getLogger("sqlalchemy").setLevel(parent)
    logging.getLogger(ENGINE_LOGGER).setLevel(engine)
    configure_logging(configured)
    assert logging.getLogger(ENGINE_LOGGER).getEffectiveLevel() == effective


def _record(name: str, level: int) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, "Row (%s)", ("hunter2",), None)


@pytest.mark.parametrize(
    "name",
    ["sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.engine.Engine.reads"],
)
def test_the_filter_drops_engine_records_below_info_whatever_the_logger_levels_say(
    name,
):
    # SQLAlchemy prints every result row at DEBUG on this logger subtree: password
    # hashes, e-mail addresses, wrapped keys, ciphertexts.
    assert not RedactionFilter().filter(_record(name, logging.DEBUG))
    assert not RedactionFilter().filter(_record(name, logging.NOTSET + 5))
    assert RedactionFilter().filter(_record(name, logging.INFO))
    assert RedactionFilter().filter(_record(name, logging.WARNING))


@pytest.mark.parametrize(
    "name",
    ["sqlalchemy.engineer", "sqlalchemy.pool", "sqlalchemy", "xolotl.platform", "root"],
)
def test_the_filter_keeps_debug_records_of_every_other_logger(name):
    assert RedactionFilter().filter(_record(name, logging.DEBUG))


class _Collect(logging.Handler):
    """Keeps what reaches it, with no filter: what SQLAlchemy really emitted."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _read_the_tenant(tenant_id) -> None:
    with tenant_session(system_ctx(tenant_id)) as s:
        _ = s.scalars(select(Tenant)).all()


def _our_handler_stream() -> io.StringIO:
    (handler,) = logging.getLogger().handlers
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    _ = handler.setStream(stream)
    return stream


def test_result_rows_never_reach_our_handler_however_the_engine_logger_is_set(
    engine: Engine, untouched_logging
):
    tenant_id = make_tenant("alpha")
    logging.getLogger(ENGINE_LOGGER).setLevel(logging.DEBUG)
    configure_logging("DEBUG")  # lifts the engine logger to INFO
    stream = _our_handler_stream()
    emitted = _Collect()  # no filter: it sees what SQLAlchemy emits, so the checks bite
    logging.getLogger().addHandler(emitted)

    _read_the_tenant(tenant_id)  # the cap alone keeps the engine logger at INFO
    assert "SELECT" in stream.getvalue()  # statements are logged
    assert not any("Row (" in message for message in emitted.messages)

    # A later setLevel, as a debugging recipe does, defeats the cap on the logger.
    logging.getLogger(ENGINE_LOGGER).setLevel(logging.DEBUG)
    _read_the_tenant(tenant_id)
    assert any("Row (" in message for message in emitted.messages)  # emitted ...
    assert "Row (" not in stream.getvalue()  # ... and dropped before our handler
    assert "SELECT" in stream.getvalue()  # while the statements still come through


def test_an_engine_that_echoes_at_debug_puts_no_rows_in_our_handler(
    engine: Engine, untouched_logging
):
    configure_logging("DEBUG")
    stream = _our_handler_stream()
    emitted = _Collect()
    # SQLAlchemy attaches a stdout handler to its engine logger unless it has one.
    engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    engine_logger.addHandler(emitted)
    echoing = create_engine(get_engine().url, echo="debug", hide_parameters=True)
    try:
        with echoing.connect() as conn:
            # The result is not in the statement text, which is logged at INFO.
            digest = conn.scalar(text("SELECT md5('echoed')"))
    finally:
        echoing.dispose()
        engine_logger.removeHandler(emitted)
    assert digest
    assert any(digest in message for message in emitted.messages)  # it did echo
    assert digest not in stream.getvalue()
    assert "Row (" not in stream.getvalue()


# ---- opaque values (plan 1b task B3) ----------------------------------------------


class Vault:
    """Any class that declares itself opaque is masked whole, with no import."""

    log_opaque = True


class Window:
    log_opaque = False


def _encrypted() -> Encrypted[str]:
    return Encrypted(
        ciphertext=b"\x00\x01" + bytes(range(40)),
        tenant_id=uuid4(),
        purpose=KeyPurpose.DATA,
        version=1,
        _codec=STR_CODEC,
    )


@pytest.mark.parametrize("make", [Vault, _encrypted])
def test_an_opaque_object_is_masked_in_every_position_of_a_record(make):
    logger, stream = _capture()
    logger.info("opened %s", make())
    assert _last_line(stream)["msg"] == "opened ***"
    logger.info("opened %(vault)s", {"vault": make()})
    assert _last_line(stream)["msg"] == "opened ***"
    logger.info("listed", extra={"vault": make(), "items": [make(), {"d": make()}]})
    line = _last_line(stream)
    assert (line["vault"], line["items"]) == ("***", ["***", {"d": "***"}])
    assert redact(make()) == "***"
    assert redact({"k": (make(),)}) == {"k": ["***"]}


def test_a_class_that_does_not_declare_itself_opaque_is_left_alone():
    window = Window()
    assert redact(window) is window
    assert redact(object) is object


def test_an_encrypted_value_is_masked_even_where_it_is_the_whole_message():
    logger, stream = _capture()
    logger.info(_encrypted())
    assert _last_line(stream)["msg"] == "Encrypted(***)"


# ---- the HTTP client's own logging -----------------------------------------------

# httpx logs every request it makes, method and full URL, at INFO. The URL of a
# webhook (or of any other service we call) often carries a token in its path or query,
# and the redaction filter cannot see inside a message.


@pytest.mark.parametrize("name", OUTBOUND_LOGGERS)
@pytest.mark.parametrize(
    ("own", "configured", "effective"),
    [
        # Lowered by someone, or following a root that is: capped at WARNING.
        (logging.DEBUG, "DEBUG", logging.WARNING),
        (logging.INFO, "INFO", logging.WARNING),
        (logging.NOTSET, "DEBUG", logging.WARNING),
        (logging.NOTSET, "INFO", logging.WARNING),
        # Nothing quieter is raised.
        (logging.WARNING, "DEBUG", logging.WARNING),
        (logging.ERROR, "DEBUG", logging.ERROR),
    ],
)
def test_the_http_clients_are_never_more_verbose_than_warning(
    untouched_logging, name, own, configured, effective
):
    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger(name).setLevel(own)
    configure_logging(configured)
    assert logging.getLogger(name).getEffectiveLevel() == effective


@pytest.mark.parametrize(
    "name",
    ["httpx", "httpcore", "httpcore.connection", "httpcore.http11", "httpcore.proxy"],
)
def test_the_filter_drops_http_client_records_below_warning(name):
    assert not RedactionFilter().filter(_record(name, logging.DEBUG))
    assert not RedactionFilter().filter(_record(name, logging.INFO))
    assert RedactionFilter().filter(_record(name, logging.WARNING))
    assert RedactionFilter().filter(_record(name, logging.ERROR))


@pytest.mark.parametrize(
    "name", ["httpxy", "httpx2", "httpcorex", "xolotl.httpx", "xolotl.platform"]
)
def test_the_filter_leaves_look_alike_loggers_alone(name):
    assert RedactionFilter().filter(_record(name, logging.INFO))


def test_a_url_with_a_token_never_reaches_our_handler(untouched_logging):
    configure_logging("DEBUG")
    stream = _our_handler_stream()
    emitted = _Collect()  # no filter: it sees what the client really emitted
    logging.getLogger().addHandler(emitted)
    url = "https://hooks.example.com/services/tok_s3cr3t?key=k_s3cr3t"

    def post() -> None:
        with respx.mock() as router:
            _ = router.post(url).mock(return_value=httpx.Response(200))
            _ = httpx.post(url, content=b"{}")

    post()  # the logger's own cap keeps the client quiet
    assert not any("tok_s3cr3t" in message for message in emitted.messages)

    # A later setLevel, as a debugging recipe does, defeats the cap on the logger.
    for name in OUTBOUND_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    post()
    assert any("tok_s3cr3t" in message for message in emitted.messages)  # emitted ...
    assert "tok_s3cr3t" not in stream.getvalue()  # ... and dropped before our handler
    assert "k_s3cr3t" not in stream.getvalue()

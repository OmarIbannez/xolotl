"""Every module router is created here so the Idempotency-Key rule cannot be skipped."""

from fastapi import APIRouter

from xolotl.platform.api.idempotency import IdempotentRoute


def v1_router(*, prefix: str = "", tags: list[str] | None = None) -> APIRouter:
    # A copy typed as FastAPI's ``list[str | Enum]``: lists are invariant.
    return APIRouter(
        prefix=prefix,
        tags=None if tags is None else [*tags],
        route_class=IdempotentRoute,
    )

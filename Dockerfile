# syntax=docker/dockerfile:1.7

# One exact Node version builds the console (jsdom, which its tests need, requires
# >= 22.22.2). CI pins the same literal in .github/workflows/ci.yml, job "console";
# tests/test_toolchain_pins.py fails when the two differ, so change them together.
ARG NODE_VERSION=22.23.3

# The staff console is static files: built here once, copied into the runtime image.
FROM node:${NODE_VERSION}-alpine AS console
WORKDIR /console
COPY console/package.json console/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci
COPY console/ ./
RUN npm run build

FROM python:3.13-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

RUN useradd --create-home --uid 10001 xolotl && chown -R xolotl:xolotl /app
# After the chown on purpose: the API only reads the console, so root keeps it.
COPY --from=console /console/dist ./src/xolotl/static
ENV XOLOTL_STATIC_DIR=/app/src/xolotl/static
USER xolotl
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status == 200 else 1)"
CMD ["xolotl", "serve"]

# syntax=docker/dockerfile:1
# Multi-stage Python app image for EAP MVP1 (API, migrate, worker).
# Pinned base images (tags + digests verified 2026-11-17):
#   python:3.12.13-slim-trixie
#   ghcr.io/astral-sh/uv:0.11.21

FROM python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.21@sha256:ff07b86af50d4d9391d9daf4ff89ce427bc544f9aae87057e69a1cc0aa369946 /uv /uvx /usr/local/bin/

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock README.md ./
COPY ppm-v2/reducer/pyproject.toml ppm-v2/reducer/pyproject.toml
COPY ppm-v2/reducer/ppm_reducer ppm-v2/reducer/ppm_reducer
COPY src src

RUN uv sync --locked --no-dev


FROM python:3.12.13-slim-trixie@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS runtime

RUN groupadd --gid 10001 eap \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin eap

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --chown=10001:10001 pyproject.toml README.md alembic.ini ./
COPY --chown=10001:10001 src ./src
COPY --chown=10001:10001 ppm-v2/reducer ./ppm-v2/reducer
COPY --chown=10001:10001 contracts ./contracts
COPY --chown=10001:10001 migrations ./migrations

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "eap.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

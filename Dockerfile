# syntax=docker/dockerfile:1.7
#
# Project Yasmina — production API image.
#
# Multi-stage build: the builder resolves wheels for the selected extras into a
# virtualenv, the runtime stage keeps only the virtualenv, the application code
# and the PostgreSQL client needed for encrypted backups. The image runs as a
# fixed non-root UID and ships pre-compiled bytecode for the application.
#
# Build (full feature set):
#   docker build -t yasmina-api:latest .
# Slim build without PDF/GAEB parsing (smaller, faster CI):
#   docker build --build-arg INSTALL_EXTRAS="ai,e-invoicing" -t yasmina-api:slim .

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder

ARG PYTHON_VERSION
ARG INSTALL_EXTRAS="ai,e-invoicing,parsing"

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip setuptools wheel \
    && if [ -n "${INSTALL_EXTRAS}" ]; then \
         python -m pip install ".[${INSTALL_EXTRAS}]"; \
       else \
         python -m pip install .; \
       fi \
    && python -m compileall -q /opt/venv/lib/python${PYTHON_VERSION}/site-packages/src

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG PYTHON_VERSION

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq5/postgresql-client power `pg_dump` backups, curl is used by the
# container healthcheck in docker-compose.prod.yml.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client curl tini \
    && rm -rf /var/lib/apt/lists/*

# The runtime does not install packages; dropping pip/setuptools removes both
# unused tooling and their CVE surface from the final image (base interpreter).
RUN /usr/local/bin/python3.12 -m pip uninstall -y pip setuptools wheel || true

RUN groupadd --gid 10001 yasmina \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin yasmina

COPY --from=builder /opt/venv /opt/venv
RUN /opt/venv/bin/python -m pip uninstall -y pip setuptools wheel || true

WORKDIR /app
COPY --chown=yasmina:yasmina src ./src
COPY --chown=yasmina:yasmina config ./config
COPY --chown=yasmina:yasmina alembic ./alembic
COPY --chown=yasmina:yasmina alembic.ini pyproject.toml README.md ./
RUN mkdir -p /app/var/exports/gaeb \
    && chown -R yasmina:yasmina /app/var \
    && python -m compileall -q /app/src /app/alembic

USER yasmina:yasmina

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

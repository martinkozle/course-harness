# syntax=docker/dockerfile:1
#
# Course Harness Classroom. Build from the repository root:
#
#   docker build -f packages/classroom/Dockerfile -t course-harness-classroom .
#
# or `docker compose build` with the root compose.yaml. See docs/deploy-classroom.md.
#
# The image never installs Course Harness (ADR 0017): it installs the Classroom with the light
# dependencies of `course_harness.core` from uv.lock, then copies only
# `course_harness/__init__.py` and `course_harness/core/` next to it.

# Base images are pinned by digest; the tag each digest was taken from is in the comment.
# python:3.14.6-slim-trixie
ARG PYTHON_IMAGE=python:3.14.6-slim-trixie@sha256:7bec7ddcddeff7975d6ba9b4be7dd6f6b2f55e7491539145e2978f7f97ce9144

# uv 0.11.28
FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv

# 1. The Classroom interface, built with the locked Bun dependencies.
# oven/bun:1.3.13
FROM oven/bun:1.3.13@sha256:87416c977a612a204eb54ab9f3927023c2a3c971f4f345a01da08ea6262ae30e AS frontend
ENV BUN_INSTALL_CACHE_DIR=/bun-cache
WORKDIR /src
COPY package.json bun.lock tsconfig.base.json ./
COPY frontend/ui/package.json frontend/ui/
COPY frontend/classroom/package.json frontend/classroom/
COPY frontend/harness/package.json frontend/harness/
RUN --mount=type=cache,target=/bun-cache bun install --frozen-lockfile
COPY frontend/ui frontend/ui
COPY frontend/classroom frontend/classroom
RUN bun run --filter @course-harness/classroom build

# 2. The Classroom and its locked Python dependencies in a virtual environment, plus core.
FROM ${PYTHON_IMAGE} AS python
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_CACHE_DIR=/uv-cache \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/classroom
WORKDIR /src
# uv reads every workspace member's pyproject.toml, but installs only the Classroom package:
# `--package` leaves Course Harness, docling and pydantic-ai out. Dependencies first, so a source
# change does not reinstall them.
COPY pyproject.toml uv.lock ./
COPY packages/harness/pyproject.toml packages/harness/
COPY packages/classroom/pyproject.toml packages/classroom/
RUN --mount=type=cache,target=/uv-cache \
    uv sync --frozen --no-dev --no-editable --no-install-workspace \
        --package course-harness-classroom
COPY packages/classroom/src packages/classroom/src
RUN --mount=type=cache,target=/uv-cache \
    uv sync --frozen --no-dev --no-editable --package course-harness-classroom
COPY packages/harness/src/course_harness/__init__.py \
     /opt/classroom/lib/python3.14/site-packages/course_harness/
COPY packages/harness/src/course_harness/core \
     /opt/classroom/lib/python3.14/site-packages/course_harness/core
RUN /opt/classroom/bin/python -m compileall -q /opt/classroom/lib/python3.14/site-packages/course_harness

# 3. The runtime: Python, Git, headless LibreOffice Impress and fonts, run as a non-root user.
FROM ${PYTHON_IMAGE}
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        fonts-crosextra-caladea \
        fonts-crosextra-carlito \
        fonts-dejavu-core \
        fonts-liberation2 \
        git \
        libreoffice-core-nogui \
        libreoffice-draw-nogui \
        libreoffice-impress-nogui \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* \
    && fc-cache --system-only --really-force \
    && groupadd --system --gid 10001 classroom \
    && useradd --system --uid 10001 --gid classroom --no-create-home --home-dir /tmp \
        --shell /usr/sbin/nologin classroom \
    && mkdir /data \
    && chown classroom:classroom /data

# LibreOffice renders untrusted templates: lock it down (no macros, active content or links)
# and run it through a wrapper that removes network access where the runtime allows it.
COPY packages/classroom/docker/classroom-hardening.xcd /usr/lib/libreoffice/share/registry/
COPY --chmod=0755 packages/classroom/docker/libreoffice /usr/local/bin/libreoffice

COPY --from=python /opt/classroom /opt/classroom
COPY --from=frontend /src/frontend/classroom/dist /opt/classroom-static
COPY packages/classroom/docker/check_image.py /opt/check_image.py

ENV PATH=/opt/classroom/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    CLASSROOM_DATA_DIR=/data \
    CLASSROOM_STATIC_DIR=/opt/classroom-static \
    CLASSROOM_RENDER_SANDBOX=auto

# Fail the build unless only core of Course Harness is importable, the Classroom app imports,
# and LibreOffice renders a slide.
USER 10001:10001
RUN python -I /opt/check_image.py --render

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
ENTRYPOINT ["course-classroom", "serve", "--host", "0.0.0.0", "--port", "8000"]

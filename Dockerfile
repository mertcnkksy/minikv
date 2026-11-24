# syntax=docker/dockerfile:1
#
# MiniKV container image.
#
#   docker build -t minikv .
#   docker run -d --name minikv -p 6380:6380 -v minikv-data:/data minikv
#
# Arguments after the image name replace the default server options, e.g.
#   docker run minikv --appendonly --appendfsync always
#
# PYTHON_IMAGE lets you build on another Python 3.12+ base image
# (for example an internal mirror); it must provide python3 with venv support.
ARG PYTHON_IMAGE=python:3.12-slim

# ---------------------------------------------------------------------------
# Build stage: install MiniKV into an isolated virtual environment.
# ---------------------------------------------------------------------------
# The tag lives in PYTHON_IMAGE (python:3.12-slim), which hadolint cannot resolve.
# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN python3 -m venv /opt/venv
COPY pyproject.toml README.md ./
COPY src ./src
RUN /opt/venv/bin/python -m pip install . \
    && /opt/venv/bin/minikv --version

# ---------------------------------------------------------------------------
# Runtime stage: only the virtual environment, run as an unprivileged user.
# ---------------------------------------------------------------------------
# hadolint ignore=DL3006
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="MiniKV" \
      org.opencontainers.image.description="A Redis-inspired key-value database in pure Python" \
      org.opencontainers.image.version="0.6.0"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --system minikv \
    && useradd --system --gid minikv --home-dir /data --no-create-home \
       --shell /usr/sbin/nologin minikv \
    && mkdir -p /data \
    && chown minikv:minikv /data

COPY --from=builder /opt/venv /opt/venv

USER minikv
WORKDIR /data
VOLUME ["/data"]
EXPOSE 6380

# The server's own client is the health probe: no extra tools needed.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["minikv", "connect", "--no-banner", "PING"]

# `docker stop` sends SIGTERM: MiniKV fsyncs the AOF, writes a final
# snapshot and exits with code 0.
STOPSIGNAL SIGTERM
ENTRYPOINT ["minikv", "serve", "--host", "0.0.0.0", "--dir", "/data"]
CMD ["--appendonly"]

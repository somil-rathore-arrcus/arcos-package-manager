# ARCoS Package Manager - the standard, reproducible way to run it.
#
#   docker compose up -d --build        # see README "Docker Deployment"
#
# Stages:
#   frontend-build  Node builds (and typechecks) the dashboard. Node exists only
#                   here; the runtime image has none.
#   base            Python, git, ssh and the application. No secret, key, token
#                   or machine-specific path is ever copied in.
#   test            base + pytest + tests:  docker build --target test .
#   runtime         base + the built dashboard; the default target.

# --- 1. dashboard -----------------------------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# tsc --noEmit && vite build: a type error fails the image build.
RUN npm run build


# --- 2. application ---------------------------------------------------------
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APM_CACHE_DIR=/var/cache/arcos-package-manager \
    APM_WORKSPACE_DIR=/var/tmp/arcos-package-manager \
    APM_PACKAGES_FILE=/app/out/packages.yaml \
    APM_APPROVALS_FILE=/app/out/approvals.yaml

# git and ssh are the point of this image: every repository operation shells
# out to them. sshpass serves only the optional password-based SSH bridge.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates sshpass \
    && rm -rf /var/lib/apt/lists/*

# GitHub's public SSH host keys, from GitHub's API over HTTPS, so github.com is
# verified (StrictHostKeyChecking=yes) without mounting a known_hosts file.
COPY docker/github-known-hosts.py /tmp/github-known-hosts.py
RUN python /tmp/github-known-hosts.py && rm /tmp/github-known-hosts.py

WORKDIR /app
# Exact versions the test suite ran against; requirements.txt states ranges.
COPY requirements.txt constraints.txt ./
RUN pip install -r requirements.txt -c constraints.txt

COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY docker/entrypoint.sh /usr/local/bin/apm-entrypoint
# `apm <command>` inside the container, the same CLI as python -m apm.
RUN printf '#!/bin/sh\nexec python -m apm "$@"\n' > /usr/local/bin/apm \
    && chmod 755 /usr/local/bin/apm /usr/local/bin/apm-entrypoint

# Unprivileged runtime user. Its uid/gid should own the host directories that
# are mounted writable (out/, the workspace) and the mounted SSH key: set
# APM_UID/APM_GID in .env (see .env.example). Application files stay root-owned
# and read-only to it; only the runtime locations are writable.
ARG APP_UID=1000
ARG APP_GID=1000
RUN if ! getent group "${APP_GID}" >/dev/null; then groupadd --gid "${APP_GID}" apm; fi \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home \
        --shell /usr/sbin/nologin apm \
    && mkdir -p /app/out "${APM_CACHE_DIR}" "${APM_WORKSPACE_DIR}" /home/apm/.ssh \
    && chown "${APP_UID}:${APP_GID}" /app/out "${APM_CACHE_DIR}" "${APM_WORKSPACE_DIR}" \
        /home/apm/.ssh \
    && chmod 700 /home/apm/.ssh


# --- 3. tests ---------------------------------------------------------------
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt -c constraints.txt
COPY tests/ ./tests/
# The deployment-contract tests read these.
COPY Dockerfile docker-compose.yml .dockerignore .env.example .gitignore ./
COPY docker/ ./docker/
USER apm
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]


# --- 4. runtime (default) ---------------------------------------------------
FROM base AS runtime
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
USER apm
EXPOSE 8080
# /health does no git and no I/O beyond answering, so a slow GitHub never
# makes a healthy container look unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["apm-entrypoint"]
CMD ["uvicorn", "apm.api.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--timeout-graceful-shutdown", "20"]

# Backend image.
#
# git and openssh-client are the point of this image: every repository operation
# shells out to them, either locally or over the configured SSH bridge.
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        git openssh-client ca-certificates sshpass \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    APM_CACHE_DIR=/var/cache/arcos-package-manager \
    APM_WORKSPACE_DIR=/var/tmp/arcos-package-manager

# `apm <command>` inside the container, the same as `python -m apm <command>`.
RUN printf '#!/bin/sh\nexec python -m apm "$@"\n' > /usr/local/bin/apm \
    && chmod 755 /usr/local/bin/apm

# Runs unprivileged. The uid is overridable so a mounted SSH key or agent socket
# from the host stays readable (APM_UID=$(id -u) on the host).
ARG APP_UID=10001
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin apm \
    && mkdir -p ${APM_CACHE_DIR} ${APM_WORKSPACE_DIR} /app/out /home/apm/.ssh \
    && chown -R apm:apm ${APM_CACHE_DIR} ${APM_WORKSPACE_DIR} /app /home/apm/.ssh \
    && chmod 700 /home/apm/.ssh


# The backend test suite, against the same image the service runs:
#   docker build --target test -t apm-test . && docker run --rm apm-test
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ ./tests/
RUN chown -R apm:apm /app/tests
USER apm
CMD ["python", "-m", "pytest", "tests", "-q"]


FROM base AS runtime
USER apm
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "apm.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

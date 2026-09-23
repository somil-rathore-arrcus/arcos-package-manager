# Backend image.
#
# git and openssh-client are the point of this image: every repository operation
# shells out to them, either locally or over the configured SSH bridge.
FROM python:3.12-slim

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

# Runs unprivileged. The uid is overridable so a mounted SSH agent socket or key
# from the host stays readable.
ARG APP_UID=10001
RUN useradd --uid ${APP_UID} --create-home --shell /usr/sbin/nologin apm \
    && mkdir -p ${APM_CACHE_DIR} ${APM_WORKSPACE_DIR} \
    && chown -R apm:apm ${APM_CACHE_DIR} ${APM_WORKSPACE_DIR} /app
USER apm

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"

CMD ["uvicorn", "apm.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

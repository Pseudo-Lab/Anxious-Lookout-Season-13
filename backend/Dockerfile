FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock
COPY backend/app /app/app
COPY backend/db /app/db
COPY backend/alembic /app/alembic
ARG APP_GIT_SHA
ARG APP_BUILT_AT
RUN APP_GIT_SHA="$APP_GIT_SHA" APP_BUILT_AT="$APP_BUILT_AT" python -m app.write_release
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-proxy-headers"]

FROM runtime AS test
USER root
COPY backend/requirements-test.lock /tmp/requirements-test.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-test.lock
COPY backend/tests /app/tests
COPY infra/hosting/db-backup.sh /app/ops/db-backup.sh
COPY --chmod=755 backend/tests/fixtures/docker /usr/local/bin/docker
USER 10001:10001
CMD ["pytest", "-q", "-p", "no:cacheprovider", "/app/tests"]

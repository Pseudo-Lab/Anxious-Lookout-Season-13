FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.lock
COPY backend/app /app/app
COPY backend/runner /app/runner
COPY backend/broker /app/broker
COPY backend/ops /app/ops
COPY backend/db /app/db
COPY backend/alembic /app/alembic
COPY backend/research_alembic /app/research_alembic
ARG APP_GIT_SHA
ARG APP_BUILT_AT
RUN APP_GIT_SHA="$APP_GIT_SHA" APP_BUILT_AT="$APP_BUILT_AT" python -m app.write_release
RUN mkdir -p /var/lib/posts/images && chown 10001:10001 /var/lib/posts/images && chmod 700 /var/lib/posts/images
USER 10001:10001
EXPOSE 8080
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-proxy-headers"]

FROM runtime AS test
USER root
COPY backend/requirements-test.lock /tmp/requirements-test.lock
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements-test.lock
COPY backend/tests /app/tests
COPY backend/contracts /app/contracts
COPY infra/hosting/db-backup.sh /app/ops/db-backup.sh
COPY --chmod=755 backend/tests/fixtures/docker /usr/local/bin/docker
COPY --chmod=755 backend/tests/fixtures/codex.mock.py /usr/local/bin/codex-fixture
RUN mkdir -p /state && chown 10001:10001 /state && chmod 700 /state
USER 10001:10001
CMD ["pytest", "-q", "-p", "no:cacheprovider", "/app/tests"]

FROM runtime AS runner
USER root
# BuildKit platform arg selects the arm64 or amd64 pin set; unset or other refuses.
ARG TARGETARCH
RUN python /app/runner/install_codex.py \
    && mkdir -p /state \
    && chown 10001:10001 /state
# #68: Codex's workspace-write sandbox. Debian bookworm's bubblewrap 0.8.0 has no --argv0, so Codex
# reaches its Linux sandbox helper through this name, visible inside the restricted read scope.
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s codex /usr/local/bin/codex-linux-sandbox
# #68: Codex system config (the `native` permission profile) and managed requirements. Root-owned
# and read-only for the runner; runner/native_policy.py refuses to start unless both equal it.
COPY --chown=0:0 --chmod=644 backend/runner/codex-etc/config.toml backend/runner/codex-etc/requirements.toml /etc/codex/
USER 10001:10001
CMD ["uvicorn", "runner.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log", "--no-proxy-headers"]

# Isolated tests of the actual pinned bundle plus existing offline protocol cases.
FROM test AS runner-test
COPY --from=runner --chmod=755 /usr/local/bin/codex /usr/local/bin/codex
COPY --from=runner --chmod=755 /usr/local/bin/codex-code-mode-host /usr/local/bin/codex-code-mode-host
COPY --from=runner /etc/codex /etc/codex

# Separate trusted host preparation image. Never used for the API deployment.
FROM runtime AS provisioner
USER root
RUN apt-get update && apt-get install -y --no-install-recommends e2fsprogs util-linux \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["python", "-B", "-m", "ops.runner_provision_worker"]

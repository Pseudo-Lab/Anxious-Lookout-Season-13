# #64 configuration B agent image (development VM only): the runner image plus bubblewrap, which
# the pinned Codex 0.160.1 Linux sandbox requires (no bundled bwrap, no landlock on this kernel).
ARG RUNNER_IMAGE=anxious-dev-runner:50ac30b
FROM ${RUNNER_IMAGE}
USER root
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends bubblewrap >/dev/null \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001

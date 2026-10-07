FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS builder
WORKDIR /webapp
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG NEXT_PUBLIC_BASE_PATH=""
ARG WEB_GIT_SHA
ARG WEB_BUILT_AT
ARG WEB_BUILD=standalone
RUN WEB_GIT_SHA="$WEB_GIT_SHA" WEB_BUILT_AT="$WEB_BUILT_AT" pnpm run version:write
RUN case "$WEB_BUILD" in \
      standalone) NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH" pnpm run build ;; \
      pages) NEXT_PUBLIC_BASE_PATH="$NEXT_PUBLIC_BASE_PATH" pnpm run build:pages ;; \
      *) exit 1 ;; \
    esac

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runner
WORKDIR /webapp
ENV NODE_ENV=production PORT=8080 HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /webapp/.next/standalone ./
COPY --from=builder --chown=node:node /webapp/.next/static ./.next/static
COPY --from=builder --chown=node:node /webapp/public ./public
USER node
EXPOSE 8080
CMD ["node", "server.js"]

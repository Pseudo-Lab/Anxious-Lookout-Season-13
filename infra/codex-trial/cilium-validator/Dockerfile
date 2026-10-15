FROM golang:1.26-bookworm@sha256:d9c68c2c51161e12fd77e4c6320687c9cd86e1af1e3ad6e6cd63ff970641453c AS build
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOMAXPROCS=1 GOMEMLIMIT=1536MiB
WORKDIR /build
COPY go.mod go.sum main.go ./
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go mod download && go mod verify && go build -mod=readonly -p 1 -trimpath -o /policy-validator .

FROM scratch
COPY --from=build /policy-validator /policy-validator
USER 10001:10001
ENTRYPOINT ["/policy-validator"]

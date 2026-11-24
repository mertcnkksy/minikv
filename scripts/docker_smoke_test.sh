#!/usr/bin/env bash
# End-to-end smoke test of the MiniKV container image.
#
# Usage:
#   scripts/docker_smoke_test.sh [IMAGE]          (default IMAGE: minikv:latest)
#
# Environment:
#   MINIKV_SMOKE_HOST_PORT  also publish the server on this host port and
#                           check that it answers from outside the container
#   MINIKV_SMOKE_RUN_ARGS   extra `docker run` arguments (e.g. "--network host")
#
# Verifies: non-root user, health check, reads and writes, graceful
# `docker stop` (exit code 0, data flushed), and data surviving a new
# container on the same volume.
set -euo pipefail

IMAGE="${1:-minikv:latest}"
NAME="minikv-smoke-$$"
VOLUME="minikv-smoke-data-$$"
HOST_PORT="${MINIKV_SMOKE_HOST_PORT:-}"
read -r -a RUN_ARGS <<< "${MINIKV_SMOKE_RUN_ARGS:-}"

step() { printf '==> %s\n' "$*"; }
fail() {
    printf 'FAIL: %s\n' "$*" >&2
    docker logs "$NAME" >&2 2>&1 || true
    exit 1
}
cleanup() {
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_container() {
    local publish=()
    if [[ -n "$HOST_PORT" ]]; then
        publish=(-p "127.0.0.1:${HOST_PORT}:6380")
    fi
    # A short health interval keeps the test fast; the probe command is the image's own.
    docker run -d --name "$NAME" "${publish[@]}" "${RUN_ARGS[@]}" \
        --health-interval 1s -v "$VOLUME:/data" "$IMAGE" >/dev/null
}

wait_healthy() {
    local status
    for _ in $(seq 1 60); do
        [[ "$(docker inspect -f '{{.State.Running}}' "$NAME")" == "true" ]] \
            || fail "container exited while starting"
        status="$(docker inspect -f '{{.State.Health.Status}}' "$NAME")"
        [[ "$status" == "healthy" ]] && return 0
        sleep 1
    done
    fail "container did not become healthy (last status: $status)"
}

cli() { docker exec "$NAME" minikv connect --no-banner "$@"; }

expect() {  # expect <description> <expected> <actual>
    [[ "$3" == "$2" ]] || fail "$1: expected [$2], got [$3]"
}

step "Image runs as an unprivileged user"
expect "configured user" "minikv" "$(docker inspect -f '{{.Config.User}}' "$IMAGE")"

step "Start container and wait for the health check"
start_container
wait_healthy
uid="$(docker exec "$NAME" id -u)"
[[ "$uid" != "0" ]] || fail "server runs as root"

step "Write and read data"
expect "SET" "OK" "$(cli SET greeting "merhaba dünya")"
expect "SET EX" "OK" "$(cli SET session x EX 3600)"
expect "GET" '"merhaba dünya"' "$(cli GET greeting)"
expect "TTL" "(integer) 3600" "$(cli TTL session)"
docker exec "$NAME" test -s /data/appendonly.aof || fail "append-only file was not written"

if [[ -n "$HOST_PORT" ]]; then
    step "Answer on host port $HOST_PORT"
    exec 3<>"/dev/tcp/127.0.0.1/$HOST_PORT"
    printf 'PING\r\n' >&3
    read -r -t 5 reply <&3 || fail "no reply on host port"
    exec 3>&-
    expect "PING via host port" "+PONG" "${reply%$'\r'}"
fi

step "Graceful docker stop"
docker stop --time 30 "$NAME" >/dev/null
expect "exit code" "0" "$(docker inspect -f '{{.State.ExitCode}}' "$NAME")"
logs="$(docker logs "$NAME" 2>&1)"
grep -q "shutdown signal received (SIGTERM)" <<< "$logs" || fail "SIGTERM was not handled gracefully"
grep -q "snapshot saved" <<< "$logs" || fail "final snapshot was not written"
grep -q "server stopped" <<< "$logs" || fail "server did not stop cleanly"
docker rm "$NAME" >/dev/null

step "Data survives a new container on the same volume"
start_container
wait_healthy
expect "GET after restart" '"merhaba dünya"' "$(cli GET greeting)"
expect "EXISTS after restart" "(integer) 1" "$(cli EXISTS session)"
# Capture first: `docker logs | grep -q` fails under pipefail when grep exits early (SIGPIPE).
logs="$(docker logs "$NAME" 2>&1)"
grep -q "loaded" <<< "$logs" || fail "data was not loaded on startup"

step "All Docker smoke tests passed"

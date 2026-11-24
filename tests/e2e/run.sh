#!/bin/sh
# End-to-end check of the Docker package. It starts both services against a fake meter.
# Run it from anywhere: sh tests/e2e/run.sh
# It needs Docker with the Compose plugin, curl and python3. It never touches a real meter or a real database.
set -eu

cd "$(dirname "$0")/../.."

export COMPOSE_PROJECT_NAME=hw-e2e
export HW_METER_URL=http://meter:8080/api/v1/data
export HW_WEB_PORT=18085
COMPOSE="docker compose -f docker-compose.yml -f tests/e2e/docker-compose.e2e.yml"
BASE="http://127.0.0.1:$HW_WEB_PORT"

fail() {
    echo "FAIL: $1"
    $COMPOSE ps -a || true
    $COMPOSE logs --no-color --tail 60 || true
    exit 1
}

cleanup() {
    $COMPOSE down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

$COMPOSE up -d --build

echo "1. /health becomes ok (the collector writes, the api reads, the volume works)"
i=0
until curl -fsS "$BASE/health" >/dev/null 2>&1; do
    i=$((i + 1))
    if [ "$i" -ge 90 ]; then fail "/health did not become ok in 90 seconds"; fi
    sleep 1
done

echo "2. /stats shows the reading of the fake meter"
curl -fsS "$BASE/stats" | python3 -c "import json, sys; sys.exit(0 if json.load(sys.stdin)['now_w'] == 500 else 1)" \
    || fail "/stats does not show 500 W"

echo "3. The pages and the history answer"
for path in / /power-quality "/history?period=day" "/api/power-quality?hours=24"; do
    curl -fsS -o /dev/null "$BASE$path" || fail "$path did not answer 200"
done

echo "4. The program does not run as root"
uid=$($COMPOSE exec -T collector id -u)
if [ "$uid" = "0" ]; then fail "the collector runs as root"; fi

echo "5. The database is in the volume"
$COMPOSE exec -T collector test -s /data/homewizard.db || fail "no database in /data"

echo "6. The collector stops cleanly on SIGTERM"
start=$(date +%s)
$COMPOSE stop -t 10 collector
elapsed=$(( $(date +%s) - start ))
code=$(docker inspect -f '{{.State.ExitCode}}' "$($COMPOSE ps -aq collector)")
if [ "$code" != "0" ]; then fail "the collector stopped with exit code $code"; fi
if [ "$elapsed" -ge 9 ]; then fail "the collector needed $elapsed seconds to stop (it was killed)"; fi

echo "OK: the Docker package works"

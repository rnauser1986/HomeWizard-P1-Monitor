#!/bin/sh
# Set up HomeWizard P1 with Docker. The script asks for what the program needs, writes .env and starts the containers.
# Run it from the repository, as a normal user who may run Docker:
#   sh install/docker-setup.sh
# HW_DOCKER can be "sudo docker" when the user may not talk to the Docker daemon.
# Options:
#   --yes       Ask nothing. Take every answer from the environment (HW_METER_URL, HW_WEB_PORT, TZ, HW_PRICE_AREA,
#               HW_SIGNAL_URL, HW_SIGNAL_FROM, HW_SIGNAL_TO). HW_METER_URL is required.
#   --no-check  Do not call the meter and do not wait for /health. For tests and for a meter that is not on yet.
# The script can run again. It never replaces an existing .env without asking, and it keeps a copy as .env.bak.
set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

ASSUME_YES=0
CHECK=1
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        --no-check) CHECK=0 ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

fail() { echo "Error: $*" >&2; exit 1; }

# ask VAR "Question" "default": the value goes into VAR. An answer already in the environment is the default.
ask() {
    var="$1"; question="$2"; default="$3"
    eval "current=\${$var:-}"
    [ -n "$current" ] && default="$current"
    if [ "$ASSUME_YES" -eq 1 ]; then
        eval "$var=\$default"
        return 0
    fi
    if [ -n "$default" ]; then
        printf '%s [%s]: ' "$question" "$default"
    else
        printf '%s: ' "$question"
    fi
    answer=""
    read -r answer || true
    [ -z "$answer" ] && answer="$default"
    eval "$var=\$answer"
}

yes_no() {
    # yes_no "Question" y|n: returns 0 for yes.
    question="$1"; default="$2"
    if [ "$ASSUME_YES" -eq 1 ]; then
        [ "$default" = "y" ]
        return
    fi
    printf '%s [%s]: ' "$question" "$default"
    answer=""
    read -r answer || true
    [ -z "$answer" ] && answer="$default"
    case "$answer" in y|Y|yes|YES|j|J) return 0 ;; *) return 1 ;; esac
}

DOCKER="${HW_DOCKER:-docker}"
command -v "${DOCKER%% *}" >/dev/null 2>&1 || fail "Docker is not installed. See https://docs.docker.com/engine/install/"
$DOCKER compose version >/dev/null 2>&1 || fail "The Docker Compose plugin is missing. See https://docs.docker.com/compose/install/"
[ -f docker-compose.yml ] || fail "docker-compose.yml is not in $REPO_DIR"

# An existing .env: keep it, or write a new one and keep the old one as .env.bak.
WRITE_ENV=1
if [ -f .env ]; then
    if [ "$ASSUME_YES" -eq 1 ]; then
        # Without --yes answers in the environment, the existing file stays.
        [ -z "${HW_METER_URL:-}" ] && WRITE_ENV=0
    elif yes_no "A .env file exists. Keep it and skip the questions?" y; then
        WRITE_ENV=0
    fi
fi

if [ "$WRITE_ENV" -eq 1 ]; then
    echo "HomeWizard P1 setup. Press Enter to take the value in brackets."

    # The meter. Accept 192.168.1.50, http://192.168.1.50 or the whole address.
    while :; do
        ask HW_METER_URL "Address of the HomeWizard P1 meter (IP address or URL)" ""
        [ -n "$HW_METER_URL" ] || { [ "$ASSUME_YES" -eq 1 ] && fail "HW_METER_URL is required"; echo "The meter address is required."; continue; }
        case "$HW_METER_URL" in http://*|https://*) ;; *) HW_METER_URL="http://$HW_METER_URL" ;; esac
        case "$HW_METER_URL" in */api/*) ;; *) HW_METER_URL="${HW_METER_URL%/}/api/v1/data" ;; esac
        if [ "$CHECK" -eq 1 ] && command -v curl >/dev/null 2>&1; then
            if curl -fsS --max-time 5 "$HW_METER_URL" >/dev/null 2>&1; then
                echo "  The meter answers."
            elif [ "$ASSUME_YES" -eq 1 ]; then
                echo "  Warning: the meter does not answer at $HW_METER_URL" >&2
            else
                echo "  The meter does not answer at $HW_METER_URL"
                yes_no "  Use this address anyway?" n || { HW_METER_URL=""; continue; }
            fi
        fi
        break
    done

    while :; do
        ask HW_WEB_PORT "Port for the web page" "8085"
        case "$HW_WEB_PORT" in
            ''|*[!0-9]*) echo "The port must be a number." ;;
            *) if [ "$HW_WEB_PORT" -ge 1 ] && [ "$HW_WEB_PORT" -le 65535 ]; then break; fi; echo "The port must be 1 to 65535." ;;
        esac
        [ "$ASSUME_YES" -eq 1 ] && fail "Invalid HW_WEB_PORT"
        HW_WEB_PORT=""
    done

    ask TZ "Time zone (for the day, week and month statistics)" "Europe/Stockholm"
    ask HW_PRICE_AREA "Electricity price area (SE1, SE2, SE3 or SE4)" "SE3"

    SIGNAL=0
    if [ "$ASSUME_YES" -eq 1 ]; then
        [ -n "${HW_SIGNAL_URL:-}" ] && SIGNAL=1
    elif yes_no "Send alarms as Signal messages (needs signal-cli-rest-api)?" n; then
        SIGNAL=1
    fi
    if [ "$SIGNAL" -eq 1 ]; then
        ask HW_SIGNAL_URL "Address of signal-cli-rest-api" ""
        ask HW_SIGNAL_FROM "Sender number (the number registered in Signal, with country code)" ""
        ask HW_SIGNAL_TO "Receiver number" ""
    else
        HW_SIGNAL_URL=""; HW_SIGNAL_FROM=""; HW_SIGNAL_TO=""
    fi

    if [ -f .env ]; then
        cp -p .env .env.bak
        echo "The old .env is saved as .env.bak"
    fi
    umask 077
    {
        echo "# Written by install/docker-setup.sh. Never put this file in the repository."
        echo "HW_METER_URL=$HW_METER_URL"
        echo "HW_WEB_PORT=$HW_WEB_PORT"
        echo "TZ=$TZ"
        echo "HW_PRICE_AREA=$HW_PRICE_AREA"
        echo "HW_SIGNAL_URL=$HW_SIGNAL_URL"
        echo "HW_SIGNAL_FROM=$HW_SIGNAL_FROM"
        echo "HW_SIGNAL_TO=$HW_SIGNAL_TO"
    } > .env
    chmod 600 .env
    echo "Wrote .env. Every other setting has a default. See .env.example to change one."
fi

# In a checkout of the repository (a Dockerfile next to the compose file) the image is built here. A release install has only
# the compose file, and the image comes from the registry.
BUILD=""
if [ -f Dockerfile ]; then
    BUILD="--build"
    echo "Starting the containers (the first build takes a minute)..."
else
    echo "Starting the containers..."
fi
$DOCKER compose up -d $BUILD

PORT="$(sed -n 's/^HW_WEB_PORT=//p' .env | tail -n 1)"
PORT="${PORT:-8085}"
HOST="$(hostname -I 2>/dev/null | sed 's/ .*//')"
HOST="${HOST:-<this host>}"

if [ "$CHECK" -eq 1 ] && command -v curl >/dev/null 2>&1; then
    printf 'Waiting for the first measurement'
    tries=0
    while [ "$tries" -lt 30 ]; do
        if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
            echo
            echo "Running. Open http://$HOST:$PORT/"
            exit 0
        fi
        printf '.'
        tries=$((tries + 1))
        sleep 2
    done
    echo
    echo "No measurement yet. Check the meter address and read the log: docker compose logs collector" >&2
    exit 1
fi
echo "Started. Open http://$HOST:$PORT/"

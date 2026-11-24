#!/bin/sh
# One-line install of HomeWizard P1 with Docker. It downloads the repository, then runs the setup script that asks for the
# meter address and starts the containers. You do not need git.
#   curl -fsSL https://raw.githubusercontent.com/rnauser1986/HomeWizard-P1-Monitor/main/install/get.sh | sh
# Options (after "sh -s --"):
#   --yes, --no-check   as for install/docker-setup.sh
#   --no-docker-install do not install Docker when it is missing, stop instead
#   --uninstall         stop and remove the containers. The data stays in the Docker volume.
#   --delete-data       with --uninstall: also delete the database and the folder of the files
# Settings in the environment:
#   HW_INSTALL_DIR  where the files go (default: $HOME/homewizard-p1)
#   HW_REPO         the GitHub repository (default: rnauser1986/HomeWizard-P1-Monitor)
#   HW_REF          the branch or tag (default: main)
#   GITHUB_TOKEN    only for a private repository
# The script can run again. It replaces the program files and keeps .env, and the database stays in the Docker volume.
set -eu

REPO="${HW_REPO:-rnauser1986/HomeWizard-P1-Monitor}"
REF="${HW_REF:-main}"
DIR="${HW_INSTALL_DIR:-${HOME:?HOME is not set}/homewizard-p1}"

fail() { echo "Error: $*" >&2; exit 1; }

for tool in curl tar; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is not installed."
done

ASSUME_YES=0; UNINSTALL=0; DELETE_DATA=0; INSTALL_DOCKER=1
for arg in "$@"; do
    case "$arg" in
        --yes) ASSUME_YES=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --delete-data) DELETE_DATA=1 ;;
        --no-docker-install) INSTALL_DOCKER=0 ;;
    esac
done

# Root needs no sudo. Everyone else needs it to install Docker and, when the user is not in the docker group, to run it.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

if ! command -v docker >/dev/null 2>&1; then
    [ "$INSTALL_DOCKER" -eq 1 ] || fail "Docker is not installed."
    if [ -n "$SUDO" ]; then
        command -v sudo >/dev/null 2>&1 || fail "Docker is not installed, and sudo is missing to install it. Install Docker, or run this as root."
    fi
    echo "Docker is not installed. Installing it with Docker's script (get.docker.com). This asks for your password."
    TMP_DOCKER="$(mktemp)"
    curl -fsSL https://get.docker.com -o "$TMP_DOCKER" || fail "Docker's install script could not be downloaded."
    $SUDO sh "$TMP_DOCKER" || fail "Docker could not be installed."
    rm -f "$TMP_DOCKER"
fi

# Use docker as it is, or with sudo when this user may not talk to the Docker daemon.
if docker info >/dev/null 2>&1; then
    HW_DOCKER="docker"
elif [ -n "$SUDO" ] && $SUDO docker info >/dev/null 2>&1; then
    HW_DOCKER="$SUDO docker"
else
    fail "Docker does not answer. Start it, or add your user to the docker group."
fi
export HW_DOCKER

if [ "$UNINSTALL" -eq 1 ]; then
    [ -f "$DIR/docker-compose.yml" ] || fail "No install found in $DIR."
    case "$DIR" in ""|"/"|"$HOME") fail "Refusing to touch $DIR." ;; esac
    if [ "$DELETE_DATA" -eq 1 ]; then
        (cd "$DIR" && $HW_DOCKER compose down -v) || fail "The containers could not be removed."
        rm -rf "$DIR"
        echo "Removed the containers, the database and $DIR."
    else
        (cd "$DIR" && $HW_DOCKER compose down) || fail "The containers could not be removed."
        echo "Removed the containers. The database is still in the Docker volume and the files are in $DIR."
        echo "Install again to get it back. Add --delete-data to delete it too."
    fi
    exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

echo "Downloading $REPO ($REF)..."
if [ -n "${GITHUB_TOKEN:-}" ]; then
    curl -fsSL -H "Authorization: Bearer $GITHUB_TOKEN" -o "$TMP/src.tar.gz" "https://api.github.com/repos/$REPO/tarball/$REF" \
        || fail "The download failed. Check the repository name and the token."
else
    curl -fsSL -o "$TMP/src.tar.gz" "https://github.com/$REPO/archive/$REF.tar.gz" \
        || fail "The download failed. A private repository needs GITHUB_TOKEN."
fi

mkdir -p "$DIR"
# The archive has one top folder. Strip it. A .env in the folder is not in the archive, so it stays.
tar -xzf "$TMP/src.tar.gz" -C "$DIR" --strip-components=1 || fail "The archive could not be unpacked."
[ -f "$DIR/install/docker-setup.sh" ] || fail "install/docker-setup.sh is not in the download."
echo "The files are in $DIR"

# The download is unpacked, so the temporary folder can go. exec would skip the trap.
rm -rf "$TMP"
trap - EXIT INT TERM

# The questions need the keyboard. When this script comes through a pipe, the keyboard is /dev/tty.
if [ "$ASSUME_YES" -eq 1 ]; then
    exec sh "$DIR/install/docker-setup.sh" "$@"
elif [ -t 0 ]; then
    exec sh "$DIR/install/docker-setup.sh" "$@"
elif (: </dev/tty) 2>/dev/null; then
    exec sh "$DIR/install/docker-setup.sh" "$@" </dev/tty
else
    fail "No keyboard to ask on. Run it again with --yes and HW_METER_URL set, or run: sh $DIR/install/docker-setup.sh"
fi

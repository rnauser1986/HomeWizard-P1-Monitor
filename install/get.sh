#!/bin/sh
# Installs HomeWizard P1 with Docker on Linux, e.g. a Raspberry Pi with Raspberry Pi OS (64-bit), Ubuntu or Debian.
#
#   curl -fsSL https://github.com/rnauser1986/HomeWizard-P1-Monitor/releases/latest/download/install.sh | sh
#
# You do not download the repository. The script installs Docker if it is missing, downloads the published image, takes the
# compose file and the setup script from the image, asks for the address of the meter (and, if you want, the Signal values),
# writes .env and starts the program. It uses sudo for what needs it, so it can ask for your password. Run it as your normal user.
# Safe to run again: it updates the compose file and the image, keeps .env, and the database stays in the Docker volume.
#
# Options (after "sh -s --"):
#   --yes, --no-check   as for the setup script (install/docker-setup.sh)
#   --no-docker-install do not install Docker when it is missing, stop instead
#   --uninstall         stop and remove the containers. The data stays in the Docker volume.
#   --delete-data       with --uninstall: also delete the database and the folder of the files
# Settings in the environment:
#   HW_INSTALL_DIR  where the files go (default: $HOME/homewizard-p1)
#   HW_VERSION      the image version to run, e.g. 2026.11.24 (default: the version of this script, else latest)
#
# This file is attached to each GitHub release as install.sh. The Release workflow fills in the version on the line RELEASE="".
# Everything is in functions, called on the last line: a download cut off halfway runs nothing.
set -eu

# The release this script is attached to (e.g. v2026.11.24). The Release workflow fills it in.
RELEASE=""

IMAGE_REPO="ghcr.io/rnauser1986/homewizard-p1"
# The container it makes to read files from the image, never started. It has a fixed name, so one a stopped run left behind
# is removed next time.
READER="homewizard-p1-install-read"

fail() { echo "Error: $*" >&2; exit 1; }

main() {
    dir="${HW_INSTALL_DIR:-${HOME:?HOME is not set}/homewizard-p1}"
    assume_yes=0; uninstall=0; delete_data=0; install_docker=1
    setup_args=""
    for arg in "$@"; do
        case "$arg" in
            --yes) assume_yes=1; setup_args="$setup_args --yes" ;;
            --no-check) setup_args="$setup_args --no-check" ;;
            --uninstall) uninstall=1 ;;
            --delete-data) delete_data=1 ;;
            --no-docker-install) install_docker=0 ;;
            *) fail "Unknown option: $arg" ;;
        esac
    done

    # The image version: HW_VERSION, else the release of this script, else the newest.
    tag="${HW_VERSION:-}"
    if [ -z "$tag" ] && [ -n "$RELEASE" ]; then tag="${RELEASE#v}"; fi
    tag="${tag:-latest}"
    image="$IMAGE_REPO:$tag"
    HW_VERSION="$tag"; export HW_VERSION

    # Root needs no sudo. Everyone else needs it to install Docker and, when the user is not in the docker group, to run it.
    sudo_cmd=""
    if [ "$(id -u)" -ne 0 ]; then sudo_cmd="sudo"; fi

    if ! command -v docker >/dev/null 2>&1; then
        [ "$install_docker" -eq 1 ] || fail "Docker is not installed."
        command -v curl >/dev/null 2>&1 || fail "Docker is not installed, and curl is missing to install it."
        if [ -n "$sudo_cmd" ]; then
            command -v sudo >/dev/null 2>&1 || fail "Docker is not installed, and sudo is missing to install it. Install Docker, or run this as root."
        fi
        echo "Docker is not installed. Installing it with Docker's script (get.docker.com). This asks for your password."
        docker_script="$(mktemp)"
        curl -fsSL https://get.docker.com -o "$docker_script" || fail "Docker's install script could not be downloaded."
        $sudo_cmd sh "$docker_script" || fail "Docker could not be installed."
        rm -f "$docker_script"
    fi

    # Use docker as it is, or with sudo when this user may not talk to the Docker daemon.
    if docker info >/dev/null 2>&1; then
        HW_DOCKER="docker"
    elif [ -n "$sudo_cmd" ] && $sudo_cmd docker info >/dev/null 2>&1; then
        HW_DOCKER="$sudo_cmd docker"
    else
        fail "Docker does not answer. Start it, or add your user to the docker group."
    fi
    export HW_DOCKER

    if [ "$uninstall" -eq 1 ]; then
        uninstall_here "$dir" "$delete_data"
        return 0
    fi

    echo "Downloading $image ..."
    $HW_DOCKER pull "$image" \
        || fail "The image could not be downloaded. Check the network. A private package needs: docker login ghcr.io"

    # Take the compose file and the setup script from the image, so they always match the program.
    $HW_DOCKER rm -f "$READER" >/dev/null 2>&1 || true
    $HW_DOCKER create --name "$READER" "$image" >/dev/null || fail "The image could not be opened."
    mkdir -p "$dir/install"
    $HW_DOCKER cp "$READER:/app/install/compose.yml" "$dir/docker-compose.yml" || fail "The compose file is not in the image."
    $HW_DOCKER cp "$READER:/app/install/docker-setup.sh" "$dir/install/docker-setup.sh" || fail "The setup script is not in the image."
    $HW_DOCKER rm -f "$READER" >/dev/null 2>&1 || true
    echo "The files are in $dir"

    # The questions need the keyboard. When this script comes through a pipe, the keyboard is /dev/tty.
    # shellcheck disable=SC2086
    if [ "$assume_yes" -eq 1 ] || [ -t 0 ]; then
        exec sh "$dir/install/docker-setup.sh" $setup_args
    elif (: </dev/tty) 2>/dev/null; then
        exec sh "$dir/install/docker-setup.sh" $setup_args </dev/tty
    else
        fail "No keyboard to ask on. Run it again with --yes and HW_METER_URL set, or run: sh $dir/install/docker-setup.sh"
    fi
}

uninstall_here() {
    dir="$1"; delete_data="$2"
    [ -f "$dir/docker-compose.yml" ] || fail "No install found in $dir."
    case "$dir" in ""|"/"|"$HOME") fail "Refusing to touch $dir." ;; esac
    if [ "$delete_data" -eq 1 ]; then
        (cd "$dir" && $HW_DOCKER compose down -v) || fail "The containers could not be removed."
        rm -rf "$dir"
        echo "Removed the containers, the database and $dir."
    else
        (cd "$dir" && $HW_DOCKER compose down) || fail "The containers could not be removed."
        echo "Removed the containers. The database is still in the Docker volume and the files are in $dir."
        echo "Install again to get it back. Add --delete-data to delete it too."
    fi
}

main "$@"

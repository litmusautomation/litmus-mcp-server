#!/bin/bash
set -e

if [ -f .env ]; then
    source .env
fi

# Bootstrap the litmus-cli binary for local (non-Docker) runs, pinned to
# the same release the Docker image installs (the Dockerfile ARG is the single
# source of truth). Skipped when LITMUS_CLI_PATH is already set (e.g. via
# .env). Failure is non-fatal: only the litmus_sdk_discover,
# litmus_sdk_read, and litmus_sdk_write fallback tools need the binary.
bootstrap_sdk_cli() {
    TAG=$(sed -n 's/^ARG LITMUS_CLI_VERSION=//p' Dockerfile)
    if [ -z "$TAG" ]; then
        echo "run.sh: could not read LITMUS_CLI_VERSION from Dockerfile" >&2
        return 1
    fi

    TARGET="$PWD/.venv/bin/litmus-cli"
    WANT="${TAG#cli-}"
    if [ -x "$TARGET" ]; then
        HAVE=$("$TARGET" --version 2>/dev/null | head -1 | awk '{print $2}')
        if [ "$HAVE" = "$WANT" ]; then
            export LITMUS_CLI_PATH="$TARGET"
            return 0
        fi
        echo "run.sh: litmus-cli $HAVE installed, Dockerfile pins $WANT; updating"
    fi

    case "$(uname -s)" in
        Darwin) OS=darwin ;;
        Linux)  OS=linux ;;
        *)
            echo "run.sh: unsupported OS '$(uname -s)' for litmus-cli bootstrap" >&2
            return 1
            ;;
    esac
    case "$(uname -m)" in
        arm64|aarch64) ARCH=arm64 ;;
        x86_64|amd64)  ARCH=amd64 ;;
        *)
            echo "run.sh: unsupported architecture '$(uname -m)' for litmus-cli bootstrap" >&2
            return 1
            ;;
    esac

    ASSET="litmus-cli-${OS}-${ARCH}"
    BASE="https://github.com/litmusautomation/litmus-sdk-releases/releases/download/${TAG}"
    TMP=$(mktemp -d)
    echo "run.sh: installing litmus-cli ${TAG} (${ASSET}) to ${TARGET}"
    if ! curl -fsSL -o "$TMP/SHA256SUMS" "$BASE/SHA256SUMS" ||
       ! curl -fsSL -o "$TMP/$ASSET" "$BASE/$ASSET"; then
        echo "run.sh: download failed for ${BASE}/${ASSET}" >&2
        rm -rf "$TMP"
        return 1
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        SHA_CMD="sha256sum"
    else
        SHA_CMD="shasum -a 256"
    fi
    if ! (cd "$TMP" && grep " ${ASSET}\$" SHA256SUMS | $SHA_CMD -c -); then
        echo "run.sh: checksum verification failed for ${ASSET}" >&2
        rm -rf "$TMP"
        return 1
    fi
    mkdir -p "$PWD/.venv/bin"
    chmod +x "$TMP/$ASSET"
    mv "$TMP/$ASSET" "$TARGET"
    rm -rf "$TMP"
    export LITMUS_CLI_PATH="$TARGET"
}

if [ -z "${LITMUS_CLI_PATH:-}" ]; then
    if ! bootstrap_sdk_cli; then
        echo "run.sh: continuing without litmus-cli; litmus_sdk_discover, litmus_sdk_read, and litmus_sdk_write will be unavailable" >&2
    fi
fi

# Prefer the project venv Python (always present in Docker, present locally after uv sync).
# This also ensures server.py (spawned as subprocess) uses the same venv via sys.executable.
if [ -f ".venv/bin/python3" ]; then
    exec .venv/bin/python3 src/web_client.py
else
    exec python3 src/web_client.py
fi

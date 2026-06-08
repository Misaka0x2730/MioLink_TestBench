#!/usr/bin/env bash
#
# Build every test_board fixture firmware variant referenced by the bench
# YAML (every (cli, swo) combo x every platform) for bench_run_tests_only.sh.
#
# Thin wrapper around scripts/targets/build_all.py with
# opinionated defaults so a typical run boils down to:
#
#     ./scripts/targets_build_all.sh
#
# Defaults (relative to the repo root):
#   --config              config/bench_pizero2w.yaml
#   --target-firmware-root  firmware/
#   --target-build-dir    firmware/build/
#
# Flags:
#   --config PATH                override the bench YAML
#   --target-firmware-root PATH  override path to firmware/ in this repo
#   --target-build-dir PATH      override test_board build dir
#   --jobs N                     forwarded to `cmake --build -j N`
#   -h, --help                   show this help and exit
#
# Before building, if firmware/external/hc32f460_ddl.zip is present,
# firmware/external/hc32f460_ddl is wiped and re-populated from the
# archive, and the archive is then removed. This lets you drop a fresh
# HC32F460 DDL release into firmware/external/ and have the next build
# pick it up automatically.
#
# Exit code: whatever build_all.py returns (stops at first
# error).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG="$REPO_ROOT/config/bench_pizero2w.yaml"
TARGET_FIRMWARE_ROOT="$REPO_ROOT/firmware"
TARGET_BUILD_DIR="$REPO_ROOT/firmware/build"
HC32F460_DDL_ZIP="$REPO_ROOT/firmware/external/hc32f460_ddl.zip"
HC32F460_DDL_DIR="$REPO_ROOT/firmware/external/hc32f460_ddl"
JOBS=""

usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG="${2:?missing value for $1}"; shift 2 ;;
        --target-firmware-root)
            TARGET_FIRMWARE_ROOT="${2:?missing value for $1}"; shift 2 ;;
        --target-build-dir)
            TARGET_BUILD_DIR="${2:?missing value for $1}"; shift 2 ;;
        --jobs|-j)
            JOBS="${2:?missing value for $1}"; shift 2 ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "       run '$0 --help' for usage" >&2
            exit 2 ;;
    esac
done

if [[ ! -f "$CONFIG" ]]; then
    echo "error: bench config not found: $CONFIG" >&2
    exit 2
fi

declare -a JOBS_FLAG=()
if [[ -n "$JOBS" ]]; then
    JOBS_FLAG=(--jobs "$JOBS")
fi

if [[ -f "$HC32F460_DDL_ZIP" ]]; then
    if ! command -v unzip >/dev/null 2>&1; then
        echo "error: 'unzip' is required to extract $HC32F460_DDL_ZIP but was not found in PATH" >&2
        exit 2
    fi
    echo "=== hc32f460_ddl: refresh vendor SDK from $(basename "$HC32F460_DDL_ZIP") ==="
    rm -rf "$HC32F460_DDL_DIR"
    mkdir -p "$HC32F460_DDL_DIR"
    unzip -q "$HC32F460_DDL_ZIP" -d "$HC32F460_DDL_DIR"
    rm -f "$HC32F460_DDL_ZIP"
fi

echo "=== build_all: build test_board fixtures ==="
python3 "$SCRIPT_DIR/targets/build_all.py" \
    --config "$CONFIG" \
    --target-firmware-root "$TARGET_FIRMWARE_ROOT" \
    --target-build-dir "$TARGET_BUILD_DIR" \
    ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}

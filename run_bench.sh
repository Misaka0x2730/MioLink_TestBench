#!/usr/bin/env bash
#
# Orchestrate the full bench workflow: build the MioLink probe images,
# build the test_board fixtures, then run the bench tests. A typical run
# boils down to:
#
#     ./run_bench.sh --miolink-firmware-root /path/to/MioLink/firmware
#
# This is a thin orchestrator that simply chains the three focused
# scripts in order:
#
#   1. ./build_miolink.sh             (MioLink probe .uf2 images)
#   2. ./build_all_test_firmware.sh   (test_board fixture firmware)
#   3. ./run_bench_tests.sh           (flash + run tests)
#
# Run those directly if you only need one phase.
#
# Defaults (relative to the directory this script lives in):
#   --config              config/bench_pi5.yaml
#   --target-firmware-root  firmware/
#   --probe-image-dir     build/probe-images/
#   --target-build-dir    firmware/build/
#
# Flags:
#   --miolink-firmware-root PATH  external MioLink probe firmware tree
#                                 (required unless --run-only; also
#                                 picked up from $MIOLINK_FIRMWARE_ROOT)
#   --config PATH                 override the bench YAML
#   --probe-image-dir PATH        override probe .uf2 staging dir
#   --target-build-dir PATH       override test_board build dir
#   --target-firmware-root PATH   override path to firmware/ in this repo
#   --jobs N                      forwarded to `cmake --build -j N`
#   --build-only                  run only the two build phases
#   --run-only                    run only run_bench_tests.sh
#   -h, --help                    show this help and exit
#   --                            terminate option parsing; remaining
#                                 arguments are forwarded verbatim to
#                                 run_bench_tests.sh (e.g.
#                                 --include-disabled, --probe SERIAL).
#
# Exit codes: whatever the delegated scripts return. The script stops at
# the first non-zero exit (set -e).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG="$SCRIPT_DIR/config/bench_pi5.yaml"
MIOLINK_FIRMWARE_ROOT="${MIOLINK_FIRMWARE_ROOT:-}"
TARGET_FIRMWARE_ROOT="$SCRIPT_DIR/firmware"
PROBE_IMAGE_DIR="$SCRIPT_DIR/build/probe-images"
TARGET_BUILD_DIR="$SCRIPT_DIR/firmware/build"
JOBS=""
PHASE="all"  # all | build | run

declare -a EXTRA_RUN_ARGS=()

usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --miolink-firmware-root)
            MIOLINK_FIRMWARE_ROOT="${2:?missing value for $1}"; shift 2 ;;
        --config)
            CONFIG="${2:?missing value for $1}"; shift 2 ;;
        --probe-image-dir)
            PROBE_IMAGE_DIR="${2:?missing value for $1}"; shift 2 ;;
        --target-build-dir)
            TARGET_BUILD_DIR="${2:?missing value for $1}"; shift 2 ;;
        --target-firmware-root)
            TARGET_FIRMWARE_ROOT="${2:?missing value for $1}"; shift 2 ;;
        --jobs|-j)
            JOBS="${2:?missing value for $1}"; shift 2 ;;
        --build-only)
            PHASE="build"; shift ;;
        --run-only)
            PHASE="run"; shift ;;
        -h|--help)
            usage; exit 0 ;;
        --)
            shift; EXTRA_RUN_ARGS=("$@"); break ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "       run '$0 --help' for usage" >&2
            exit 2 ;;
    esac
done

if [[ "$PHASE" != "run" && -z "$MIOLINK_FIRMWARE_ROOT" ]]; then
    echo "error: --miolink-firmware-root (or \$MIOLINK_FIRMWARE_ROOT) is required for the build phase" >&2
    exit 2
fi

if [[ ! -f "$CONFIG" ]]; then
    echo "error: bench config not found: $CONFIG" >&2
    exit 2
fi

declare -a JOBS_FLAG=()
if [[ -n "$JOBS" ]]; then
    JOBS_FLAG=(--jobs "$JOBS")
fi

if [[ "$PHASE" != "run" ]]; then
    "$SCRIPT_DIR/build_miolink.sh" \
        --config "$CONFIG" \
        --miolink-firmware-root "$MIOLINK_FIRMWARE_ROOT" \
        --probe-image-dir "$PROBE_IMAGE_DIR" \
        ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}

    echo
    "$SCRIPT_DIR/build_all_test_firmware.sh" \
        --config "$CONFIG" \
        --target-firmware-root "$TARGET_FIRMWARE_ROOT" \
        --target-build-dir "$TARGET_BUILD_DIR" \
        ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}
fi

if [[ "$PHASE" != "build" ]]; then
    declare -a FORWARD=()
    if [[ ${#EXTRA_RUN_ARGS[@]} -gt 0 ]]; then
        FORWARD=(-- "${EXTRA_RUN_ARGS[@]}")
    fi
    echo
    "$SCRIPT_DIR/run_bench_tests.sh" \
        --config "$CONFIG" \
        --probe-image-dir "$PROBE_IMAGE_DIR" \
        --target-build-dir "$TARGET_BUILD_DIR" \
        ${FORWARD[@]+"${FORWARD[@]}"}
fi

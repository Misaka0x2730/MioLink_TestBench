#!/usr/bin/env bash
#
# Build all bench firmware (MioLink probe + test_board fixtures) and
# run the bench tests against the configuration in the bench YAML.
#
# Thin wrapper around scripts/bench/build_bench.py and
# scripts/bench/run_bench.py with opinionated defaults so a typical run
# boils down to:
#
#     ./run_bench.sh --miolink-firmware-root /path/to/MioLink/firmware
#
# Defaults (relative to the directory this script lives in):
#   --config              config/bench_pi5.yaml
#   --target-firmware-root  firmware/
#   --probe-image-dir     build/probe-images/
#   --target-build-dir    firmware/build/
#
# Flags:
#   --miolink-firmware-root PATH  external MioLink probe firmware tree
#                                 (required for the build phase; also
#                                 picked up from $MIOLINK_FIRMWARE_ROOT)
#   --config PATH                 override the bench YAML
#   --probe-image-dir PATH        override probe .uf2 staging dir
#   --target-build-dir PATH       override test_board build dir
#   --target-firmware-root PATH   override path to firmware/ in this repo
#   --jobs N                      forwarded to `cmake --build -j N`
#   --build-only                  run only build_bench.py
#   --run-only                    run only run_bench.py
#   -h, --help                    show this help and exit
#   --                            terminate option parsing; remaining
#                                 arguments are forwarded verbatim to
#                                 run_bench.py (e.g. --include-disabled,
#                                 --no-target-flash, --probe SERIAL).
#
# Before the build phase, if firmware/external/hc32f460_ddl.zip is
# present, firmware/external/hc32f460_ddl is wiped and re-populated from
# the archive, and the archive is then removed. This lets you drop a
# fresh HC32F460 DDL release into firmware/external/ and have the next
# build pick it up automatically.
#
# Exit codes: whatever build_bench.py / run_bench.py return. The script
# stops at the first non-zero exit (set -e).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG="$SCRIPT_DIR/config/bench_pi5.yaml"
MIOLINK_FIRMWARE_ROOT="${MIOLINK_FIRMWARE_ROOT:-}"
TARGET_FIRMWARE_ROOT="$SCRIPT_DIR/firmware"
PROBE_IMAGE_DIR="$SCRIPT_DIR/build/probe-images"
TARGET_BUILD_DIR="$SCRIPT_DIR/firmware/build"
HC32F460_DDL_ZIP="$SCRIPT_DIR/firmware/external/hc32f460_ddl.zip"
HC32F460_DDL_DIR="$SCRIPT_DIR/firmware/external/hc32f460_ddl"
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

if [[ "$PHASE" != "run" && -f "$HC32F460_DDL_ZIP" ]]; then
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

if [[ "$PHASE" != "run" ]]; then
    echo "=== build_bench: build probe images + target fixtures ==="
    python3 "$SCRIPT_DIR/scripts/bench/build_bench.py" \
        --config "$CONFIG" \
        --miolink-firmware-root "$MIOLINK_FIRMWARE_ROOT" \
        --target-firmware-root "$TARGET_FIRMWARE_ROOT" \
        --probe-image-dir "$PROBE_IMAGE_DIR" \
        --target-build-dir "$TARGET_BUILD_DIR" \
        ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}
fi

if [[ "$PHASE" != "build" ]]; then
    echo
    echo "=== run_bench: flash probes + targets, run tests ==="
    python3 "$SCRIPT_DIR/scripts/bench/run_bench.py" \
        --config "$CONFIG" \
        --probe-image-dir "$PROBE_IMAGE_DIR" \
        --target-build-dir "$TARGET_BUILD_DIR" \
        ${EXTRA_RUN_ARGS[@]+"${EXTRA_RUN_ARGS[@]}"}
fi

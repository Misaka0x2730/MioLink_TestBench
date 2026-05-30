#!/usr/bin/env bash
#
# Build every MioLink probe firmware variant referenced by the bench YAML
# and stage the resulting MioLink.uf2 images for run_bench_tests.sh.
#
# Thin wrapper around scripts/build/build_miolink.py with opinionated
# defaults so a typical run boils down to:
#
#     ./build_miolink.sh --miolink-firmware-root /path/to/MioLink/firmware
#
# Defaults (relative to the directory this script lives in):
#   --config            config/bench_pi5.yaml
#   --probe-image-dir   build/probe-images/
#
# Flags:
#   --miolink-firmware-root PATH  external MioLink probe firmware tree
#                                 (required; also picked up from
#                                 $MIOLINK_FIRMWARE_ROOT)
#   --config PATH                 override the bench YAML
#   --probe-image-dir PATH        override probe .uf2 staging dir
#   --jobs N                      forwarded to `cmake --build -j N`
#   -h, --help                    show this help and exit
#
# Exit code: whatever build_miolink.py returns (stops at first error).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CONFIG="$SCRIPT_DIR/config/bench_pi5.yaml"
MIOLINK_FIRMWARE_ROOT="${MIOLINK_FIRMWARE_ROOT:-}"
PROBE_IMAGE_DIR="$SCRIPT_DIR/build/probe-images"
JOBS=""

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

if [[ -z "$MIOLINK_FIRMWARE_ROOT" ]]; then
    echo "error: --miolink-firmware-root (or \$MIOLINK_FIRMWARE_ROOT) is required" >&2
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

echo "=== build_miolink: build MioLink probe images ==="
python3 "$SCRIPT_DIR/scripts/build/build_miolink.py" \
    --config "$CONFIG" \
    --miolink-firmware-root "$MIOLINK_FIRMWARE_ROOT" \
    --probe-image-dir "$PROBE_IMAGE_DIR" \
    ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}

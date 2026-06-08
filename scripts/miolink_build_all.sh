#!/usr/bin/env bash
#
# Build every MioLink probe firmware variant referenced by the bench YAML
# and stage the resulting MioLink.uf2 images for bench_run_tests_only.sh.
#
# Thin wrapper around scripts/miolink/build_all.py with opinionated
# defaults so a typical run boils down to:
#
#     ./scripts/miolink_build_all.sh --miolink-firmware-root /path/to/MioLink/firmware
#
# Defaults (relative to the repo root):
#   --config            config/bench_pizero2w.yaml
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
# Exit code: whatever build_all.py returns (stops at first error).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG="$REPO_ROOT/config/bench_pizero2w.yaml"
MIOLINK_FIRMWARE_ROOT="${MIOLINK_FIRMWARE_ROOT:-}"
PROBE_IMAGE_DIR="$REPO_ROOT/build/probe-images"
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

echo "=== build_all: build MioLink probe images ==="
python3 "$SCRIPT_DIR/miolink/build_all.py" \
    --config "$CONFIG" \
    --miolink-firmware-root "$MIOLINK_FIRMWARE_ROOT" \
    --probe-image-dir "$PROBE_IMAGE_DIR" \
    ${JOBS_FLAG[@]+"${JOBS_FLAG[@]}"}

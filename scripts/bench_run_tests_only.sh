#!/usr/bin/env bash
#
# Flash the staged probe images + run the bench tests against the
# configuration in the bench YAML. Does not build anything: run
# scripts/miolink_build_all.sh and scripts/targets_build_all.sh first.
#
# Thin wrapper around scripts/bench/run_bench.py with opinionated
# defaults so a typical run boils down to:
#
#     ./scripts/bench_run_tests_only.sh
#
# Defaults (relative to the repo root):
#   --config            config/bench_pizero2w.yaml
#   --probe-image-dir   build/probe-images/
#   --target-build-dir  firmware/build/
#
# Flags:
#   --config PATH             override the bench YAML
#   --probe-image-dir PATH    override probe .uf2 staging dir
#   --target-build-dir PATH   override test_board build dir
#   -h, --help                show this help and exit
#   --                        terminate option parsing; remaining
#                             arguments are forwarded verbatim to
#                             run_bench.py (e.g. --include-disabled,
#                             --no-target-flash, --probe SERIAL).
#
# Exit code: whatever run_bench.py returns.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG="$REPO_ROOT/config/bench_pizero2w.yaml"
PROBE_IMAGE_DIR="$REPO_ROOT/build/probe-images"
TARGET_BUILD_DIR="$REPO_ROOT/firmware/build"

declare -a EXTRA_RUN_ARGS=()

usage() {
    sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG="${2:?missing value for $1}"; shift 2 ;;
        --probe-image-dir)
            PROBE_IMAGE_DIR="${2:?missing value for $1}"; shift 2 ;;
        --target-build-dir)
            TARGET_BUILD_DIR="${2:?missing value for $1}"; shift 2 ;;
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

if [[ ! -f "$CONFIG" ]]; then
    echo "error: bench config not found: $CONFIG" >&2
    exit 2
fi

echo "=== run_bench_tests: flash probes + targets, run tests ==="
python3 "$SCRIPT_DIR/bench/run_bench.py" \
    --config "$CONFIG" \
    --probe-image-dir "$PROBE_IMAGE_DIR" \
    --target-build-dir "$TARGET_BUILD_DIR" \
    ${EXTRA_RUN_ARGS[@]+"${EXTRA_RUN_ARGS[@]}"}

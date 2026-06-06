#!/usr/bin/env python3
"""Build every test_board fixture firmware variant the bench runner consumes.

Reads the bench YAML and, for every unique ``(cli_mode, swo_state)`` pair
referenced by the bench, builds the ``test_board_<platform>`` fixture
firmware for every platform under ``<target-firmware-root>/platforms/``
into ``<target-build-dir>/<cli-cli_mode>_swo-<swo_state>/`` with
``-DCONFIG_CLI=...`` and ``-DCONFIG_TEST_SWO=ON/OFF``. The sub-directory
layout (``bench_config.target_build_subdir``) matches what
``run_bench.py`` reads back.

Disabled connections in the YAML are still built — flipping ``enabled``
back on should not require a rebuild. Pass the same ``--target-build-dir``
to ``run_bench.py`` so the runner finds the artefacts produced here.

Usage:
    python scripts/build/build_all_test_firmware.py \\
        --config config/bench_pi5.yaml \\
        --target-firmware-root /path/to/MioLink_TestBench/firmware \\
        --target-build-dir /path/to/MioLink_TestBench/firmware/build
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# This script lives in scripts/build/ and is run directly. Put scripts/
# on sys.path for the targets/ and build/ packages, and scripts/bench/
# for the shared bench_config module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "bench"))

import targets
from build.cmake_build import BuildError, positive_int
from bench_config import (
    _CLI_TO_CMAKE,
    load_bench_config,
    target_build_subdir,
)

# Maps a YAML ``swo`` state to the ``CONFIG_TEST_SWO`` CMake value that
# enables / disables the periodic SWO trace output in the test_board
# fixture firmware.
_SWO_TO_CMAKE = {"connected": "ON", "not_present": "OFF"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build every test_board fixture firmware variant referenced "
            "by the bench config for run_bench.py."
        ),
    )
    parser.add_argument(
        "--config", type=Path, required=True,
        help="Path to the bench YAML config (same one used by run_bench.py).",
    )
    parser.add_argument(
        "--target-firmware-root", type=Path, required=True,
        help=(
            "Path to the MioLink_TestBench local firmware source tree "
            "(the directory containing the top-level CMakeLists.txt and "
            "platforms/)."
        ),
    )
    parser.add_argument(
        "--target-build-dir", type=Path, required=True,
        help=(
            "CMake build dir for the test_board fixture firmware. The "
            "same value is later passed to run_bench.py "
            "--target-build-dir."
        ),
    )
    parser.add_argument(
        "--jobs", type=positive_int, default=None,
        help="Parallel build jobs forwarded to `cmake --build -j`.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    try:
        config = load_bench_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Enumerate unique (cli_mode, swo_state) combos across all
    # connections. Each combo gets its own CMake configure + build into
    # a per-combo sub-directory of --target-build-dir.
    target_combos: set[tuple[str, str]] = set()
    for connection in config.connections:
        for cli_mode in connection.cli:
            target_combos.add((cli_mode, connection.swo))

    if not target_combos:
        print(
            "error: no target firmware combos derived from bench config",
            file=sys.stderr,
        )
        return 2

    print(
        f"[info] building test_board fixture firmware: "
        f"{len(target_combos)} (cli, swo) combo(s) × every platform "
        f"under {args.target_firmware_root}/platforms/"
    )
    for cli_mode, swo_state in sorted(target_combos):
        sub = target_build_subdir(cli_mode, swo_state)
        combo_build_dir = args.target_build_dir / sub
        cmake_defines = {
            "CONFIG_CLI": _CLI_TO_CMAKE[cli_mode],
            "CONFIG_TEST_SWO": _SWO_TO_CMAKE[swo_state],
        }
        print(
            f"\n[info] target combo cli={cli_mode} swo={swo_state} → "
            f"{combo_build_dir}"
        )
        try:
            targets.build_target_firmware(
                firmware_root=args.target_firmware_root,
                platform=None,
                build_type="Debug",
                build_dir=combo_build_dir,
                jobs=args.jobs,
                extra_cmake_defines=cmake_defines,
            )
        except (FileNotFoundError, ValueError, BuildError) as exc:
            print(
                f"error: building target firmware ({cli_mode}, {swo_state}): "
                f"{exc}",
                file=sys.stderr,
            )
            return 1

    print("\n[ ok ] test_board fixture firmware ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())

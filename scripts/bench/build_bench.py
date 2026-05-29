#!/usr/bin/env python3
"""Rebuild every firmware artefact the MioLink_TestSetup bench runner consumes.

Reads the bench YAML and drives both firmware-build helpers:

  * For each unique ``(board, flavour)`` pair returned by
    ``run_bench.variants_for(connection)`` across the configured
    probes, builds the MioLink probe firmware and stages the resulting
    ``MioLink.uf2`` under ``--probe-image-dir`` using
    ``--probe-image-pattern``. The pattern matches the one
    ``run_bench.py`` reads from later.
  * For every unique ``(cli_mode, swo_state)`` pair referenced by the
    bench, builds the ``test_board_<platform>`` fixture firmware for
    every platform under ``<target-firmware-root>/platforms/`` into
    ``<target-build-dir>/<cli-cli_mode>_swo-<swo_state>/`` with
    ``-DCONFIG_CLI=...`` and ``-DCONFIG_TEST_SWO=ON/OFF``. The
    sub-directory layout matches what ``run_bench.py`` reads back.

Disabled connections in the YAML are still built — flipping ``enabled``
back on should not require a rebuild. Pass the same
``--probe-image-dir``, ``--probe-image-pattern`` and
``--target-build-dir`` to ``run_bench.py`` so the runner finds the
artefacts produced here.

Usage:
    python scripts/bench/build_bench.py \\
        --config config/bench_pi5.yaml \\
        --miolink-firmware-root /path/to/MioLink/firmware \\
        --target-firmware-root /path/to/MioLink_TestSetup/firmware \\
        --probe-image-dir /path/to/probe-images \\
        --target-build-dir /path/to/MioLink_TestSetup/firmware/build
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Sibling-package imports: build_bench lives in scripts/bench/ and needs
# scripts/ on sys.path to import miolink/, targets/, build/. The bench/
# directory itself is also added so the sibling run_bench module resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import miolink
import targets
from build.cmake_build import BuildError, positive_int
from run_bench import (
    BuildVariant,
    _CLI_TO_CMAKE,
    load_bench_config,
    resolve_uf2,
    target_build_subdir,
    variants_for,
)

# Maps the lower-case flavour names used by run_bench's variant model
# (``"debug"`` / ``"release"``) to the ``CMAKE_BUILD_TYPE`` values the
# build helpers expect.
_FLAVOUR_TO_BUILD_TYPE = {"debug": "Debug", "release": "Release"}

# Maps a YAML ``swo`` state to the ``CONFIG_TEST_SWO`` CMake value that
# enables / disables the periodic SWO trace output in the test_board
# fixture firmware.
_SWO_TO_CMAKE = {"connected": "ON", "not_present": "OFF"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild every firmware artefact the bench runner consumes "
            "(MioLink probe .uf2 files + test_board fixture ELFs)."
        ),
    )
    parser.add_argument(
        "--config", type=Path, required=True,
        help="Path to the bench YAML config (same one used by run_bench.py).",
    )
    parser.add_argument(
        "--miolink-firmware-root", type=Path, required=True,
        help=(
            "Path to the external MioLink probe firmware source tree "
            "(the directory containing the top-level CMakeLists.txt of "
            "the MioLink repository)."
        ),
    )
    parser.add_argument(
        "--target-firmware-root", type=Path, required=True,
        help=(
            "Path to the MioLink_TestSetup local firmware source tree "
            "(the directory containing the top-level CMakeLists.txt and "
            "platforms/)."
        ),
    )
    parser.add_argument(
        "--probe-image-dir", type=Path, required=True,
        help=(
            "Staging directory for built MioLink probe .uf2 images. "
            "The same value is later passed to run_bench.py "
            "--probe-image-dir."
        ),
    )
    parser.add_argument(
        "--probe-image-pattern", type=str,
        default="MioLink-{board}-{flavour}.uf2",
        help=(
            "Template for the probe image filename under "
            "--probe-image-dir. Placeholders: {board}, {flavour}. "
            "Default: 'MioLink-{board}-{flavour}.uf2'. Must match the "
            "value passed to run_bench.py."
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

    miolink_variants: set[tuple[str, str]] = set()
    for connection in config.connections:
        for variant in variants_for(connection):
            miolink_variants.add((variant.board, variant.flavour))

    if not miolink_variants:
        print(
            "error: no MioLink variants derived from bench config; "
            "nothing to build",
            file=sys.stderr,
        )
        return 2

    args.probe_image_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"[info] building {len(miolink_variants)} MioLink variant(s) "
        f"from {args.config}"
    )
    for board, flavour in sorted(miolink_variants):
        build_type = _FLAVOUR_TO_BUILD_TYPE[flavour]
        try:
            uf2_src = miolink.build_miolink_firmware(
                firmware_root=args.miolink_firmware_root,
                board=board,
                build_type=build_type,
                jobs=args.jobs,
            )
        except (FileNotFoundError, ValueError, BuildError) as exc:
            print(
                f"error: building MioLink {board}/{flavour}: {exc}",
                file=sys.stderr,
            )
            return 1

        dest = resolve_uf2(
            args.probe_image_dir, args.probe_image_pattern,
            BuildVariant(board=board, flavour=flavour),
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(uf2_src, dest)
        print(f"[ ok ] staged {dest}")

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
        f"\n[info] building test_board fixture firmware: "
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

    print("\n[ ok ] bench firmware ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())

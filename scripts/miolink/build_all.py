#!/usr/bin/env python3
"""Build every MioLink probe firmware variant the bench runner consumes.

Reads the bench YAML and, for each unique ``(board, flavour)`` pair
returned by ``bench_config.variants_for(connection)`` across the
configured probes, builds the MioLink probe firmware and stages the
resulting ``MioLink.uf2`` under ``--probe-image-dir`` using the fixed
``MioLink-<board>-<flavour>.uf2`` name ``run_bench.py`` reads back later.

Disabled connections in the YAML are still built — flipping ``enabled``
back on should not require a rebuild. Pass the same ``--probe-image-dir``
to ``run_bench.py`` so the runner finds the artefacts produced here.

Usage:
    python scripts/miolink/build_all.py \\
        --config config/bench_pizero2w.yaml \\
        --miolink-firmware-root /path/to/MioLink/firmware \\
        --probe-image-dir /path/to/probe-images
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# This script lives in scripts/miolink/ and is run directly. Put scripts/
# on sys.path for the miolink/ and cmake/ packages, and scripts/bench/
# for the shared bench_config module.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "bench"))

import miolink
from cmake.cmake_build import BuildError, positive_int
from bench_config import (
    BuildVariant,
    load_bench_config,
    resolve_uf2,
    variants_for,
)

# Maps the lower-case flavour names used by the bench variant model
# (``"debug"`` / ``"release"``) to the ``CMAKE_BUILD_TYPE`` values the
# build helpers expect.
_FLAVOUR_TO_BUILD_TYPE = {"debug": "Debug", "release": "Release"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build every MioLink probe .uf2 image referenced by the bench "
            "config and stage it for run_bench.py."
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
        "--probe-image-dir", type=Path, required=True,
        help=(
            "Staging directory for built MioLink probe .uf2 images. "
            "The same value is later passed to run_bench.py "
            "--probe-image-dir."
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
            args.probe_image_dir,
            BuildVariant(board=board, flavour=flavour),
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(uf2_src, dest)
        print(f"[ ok ] staged {dest}")

    print("\n[ ok ] MioLink probe images ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""MioLink firmware rebuild script.

Thin MioLink-specific wrapper around the shared
:func:`build.cmake_build.cmake_rebuild` driver. Wipes a build directory,
re-runs CMake configure with the requested ``PICO_BOARD`` /
``CMAKE_BUILD_TYPE``, builds, and returns the path to ``MioLink.uf2``.

The firmware source tree lives in a separate repository from
MioLink_TestBench; pass its path via ``--firmware-root`` or set the
``MIOLINK_FIRMWARE_ROOT`` environment variable.

Usage as module:
    from miolink import build_miolink_firmware

    uf2 = build_miolink_firmware(
        firmware_root=Path("/path/to/MioLink/firmware"),
        board="miolink",
        build_type="Release",
    )

Usage as CLI:
    python miolink/build.py --firmware-root /path/to/firmware \\
        --board miolink --build-type Release
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Allow `from build.cmake_build import ...` whether this file is run
# as a standalone CLI or imported as `miolink.build` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build.cmake_build import BuildError, cmake_rebuild, positive_int

# ── Constants ────────────────────────────────────────────────────────

_BUILD_TYPES = ("Debug", "Release", "RelWithDebInfo", "MinSizeRel")

_FIRMWARE_ROOT_ENV = "MIOLINK_FIRMWARE_ROOT"

_UF2_NAME = "MioLink.uf2"


# ── Public API ───────────────────────────────────────────────────────


def default_build_dir(firmware_root: Path, board: str, build_type: str) -> Path:
    """Return the conventional build directory for (*board*, *build_type*).

    Produces ``<firmware_root>/build-<board>-<build_type_lower>``.
    """
    return firmware_root / f"build-{board}-{build_type.lower()}"


def build_miolink_firmware(
    firmware_root: Path,
    board: str,
    build_type: str = "Debug",
    build_dir: Path | None = None,
    jobs: int | None = None,
    extra_cmake_defines: dict[str, str] | None = None,
) -> Path:
    """Rebuild MioLink firmware from scratch for a given (board, build_type).

    Wipes *build_dir*, runs ``cmake -S <firmware_root> -B <build_dir>``
    with ``CMAKE_BUILD_TYPE`` + ``PICO_BOARD`` defines, then
    ``cmake --build <build_dir>``. Returns the path to the produced
    ``MioLink.uf2``.

    Args:
        firmware_root: Path to the MioLink firmware source tree
            (the directory containing the top-level ``CMakeLists.txt``).
        board: ``PICO_BOARD`` value (e.g. ``miolink``, ``miolink_pico``,
            ``pico``, ``pico_w``, ``pico2_w``, ``auto_rp2040``).
        build_type: ``CMAKE_BUILD_TYPE`` value. Default: ``Debug``.
        build_dir: Override the build directory location. Default:
            :func:`default_build_dir`.
        jobs: Parallel build jobs (``cmake --build -j <N>``). ``None``
            lets CMake decide.
        extra_cmake_defines: Extra ``-D`` cache variables merged into
            the configure command after the MioLink defaults.

    Returns:
        Path to ``<build_dir>/MioLink.uf2``.

    Raises:
        FileNotFoundError: ``firmware_root`` is missing or doesn't look
            like a firmware source tree.
        BuildError: CMake configure or build returned a non-zero exit
            code, or the expected ``MioLink.uf2`` was not produced.
        ValueError: invalid *build_type* or unsafe *build_dir*.
    """
    if build_type not in _BUILD_TYPES:
        raise ValueError(
            f"invalid build_type '{build_type}'; expected one of "
            f"{', '.join(_BUILD_TYPES)}"
        )

    firmware_root = firmware_root.resolve()

    if build_dir is None:
        build_dir = default_build_dir(firmware_root, board, build_type)
    build_dir = build_dir.resolve()

    defines: dict[str, str] = {
        "CMAKE_BUILD_TYPE": build_type,
        "PICO_BOARD": board,
    }
    if extra_cmake_defines:
        defines.update(extra_cmake_defines)

    uf2_path = build_dir / _UF2_NAME

    cmake_rebuild(
        source_dir=firmware_root,
        build_dir=build_dir,
        cmake_defines=defines,
        jobs=jobs,
        expected_artefacts=[uf2_path],
    )

    print(f"[ ok ] built {uf2_path}")
    return uf2_path


# ── CLI ──────────────────────────────────────────────────────────────


def _resolve_firmware_root(arg: str | None) -> Path:
    """Resolve firmware_root from CLI arg, env var, or fail."""
    if arg:
        return Path(arg).expanduser()
    env_value = os.environ.get(_FIRMWARE_ROOT_ENV)
    if env_value:
        return Path(env_value).expanduser()
    raise SystemExit(
        f"error: pass --firmware-root or set {_FIRMWARE_ROOT_ENV}"
    )


def _parse_define(value: str) -> tuple[str, str]:
    """Parse a KEY=VALUE pair from ``-D KEY=VALUE``."""
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"invalid -D value '{value}'; expected KEY=VALUE"
        )
    key, _, val = value.partition("=")
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError(
            f"invalid -D value '{value}'; KEY is empty"
        )
    return key, val


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild MioLink firmware from scratch for a given "
            "(PICO_BOARD, CMAKE_BUILD_TYPE) combination."
        ),
    )
    parser.add_argument(
        "--firmware-root", type=str, default=None,
        help=(
            "Path to the MioLink firmware source tree. Falls back to "
            f"the {_FIRMWARE_ROOT_ENV} env var if omitted."
        ),
    )
    parser.add_argument(
        "--board", required=True,
        help=(
            "PICO_BOARD value (e.g. miolink, miolink_pico, pico, "
            "pico_w, pico2_w, auto_rp2040)."
        ),
    )
    parser.add_argument(
        "--build-type", choices=list(_BUILD_TYPES), default="Debug",
        help="CMAKE_BUILD_TYPE (default: Debug).",
    )
    parser.add_argument(
        "--build-dir", type=Path, default=None,
        help=(
            "Override the build directory. Default: "
            "<firmware_root>/build-<board>-<build_type_lower>."
        ),
    )
    parser.add_argument(
        "--jobs", type=positive_int, default=None,
        help="Parallel build jobs forwarded to `cmake --build -j`.",
    )
    parser.add_argument(
        "-D", "--cmake-define", action="append", default=[],
        type=_parse_define, metavar="VAR=VAL",
        help=(
            "Extra `-DVAR=VAL` cache variable forwarded to cmake "
            "configure. Repeatable."
        ),
    )

    args = parser.parse_args()

    firmware_root = _resolve_firmware_root(args.firmware_root)
    extra_defines = dict(args.cmake_define)

    try:
        uf2 = build_miolink_firmware(
            firmware_root=firmware_root,
            board=args.board,
            build_type=args.build_type,
            build_dir=args.build_dir,
            jobs=args.jobs,
            extra_cmake_defines=extra_defines,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"\nUF2: {uf2}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

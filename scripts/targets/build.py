"""MioLink_TestSetup target firmware rebuild script.

Thin target-firmware wrapper around the shared
:func:`build.cmake_build.cmake_rebuild` driver. Wipes a build directory,
re-runs CMake configure for the MioLink_TestSetup source tree, builds
``test_board_<platform>`` for one or all platforms, and returns the
path(s) to the produced ``.elf`` file(s).

The default build directory is ``<firmware_root>/build`` for Debug and
``<firmware_root>/build-<type_lower>`` for any other flavour. Use
``--build-dir`` (or the *build_dir* kwarg) to override this — for
example to keep multiple build flavours side-by-side or to match where
the bench runner looks for ``test_board_<platform>.elf``.

Usage as module:
    from targets import build_target_firmware

    elf_paths = build_target_firmware(
        firmware_root=Path("/path/to/MioLink_TestSetup/firmware"),
        platform="stm32f103",
        build_type="Debug",
    )

Usage as CLI:
    python targets/build.py --firmware-root /path/to/firmware \\
        --platform stm32f103 --build-type Debug
    python targets/build.py --firmware-root /path/to/firmware \\
        --build-type Release   # build every platform
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `from build.cmake_build import ...` whether this file is run
# as a standalone CLI or imported as `targets.build` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build.cmake_build import BuildError, cmake_rebuild, positive_int

# ── Constants ────────────────────────────────────────────────────────

_BUILD_TYPES = ("Debug", "Release", "RelWithDebInfo", "MinSizeRel")


def _discover_platforms(platforms_dir: Path) -> tuple[str, ...]:
    if not platforms_dir.is_dir():
        return ()
    return tuple(sorted(
        entry.name for entry in platforms_dir.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
    ))


# ── Public API ───────────────────────────────────────────────────────


def default_build_dir(firmware_root: Path, build_type: str) -> Path:
    """Return the conventional build directory for *build_type*.

    Produces ``<firmware_root>/build`` for ``Debug`` and
    ``<firmware_root>/build-<type_lower>`` for any other flavour.
    """
    if build_type == "Debug":
        return firmware_root / "build"
    return firmware_root / f"build-{build_type.lower()}"


def expected_elf(build_dir: Path, platform: str) -> Path:
    """Return the path to the produced ``test_board_<platform>.elf``."""
    name = f"test_board_{platform}"
    return build_dir / "test_boards" / name / f"{name}.elf"


def build_target_firmware(
    firmware_root: Path,
    platform: str | None = None,
    build_type: str = "Debug",
    build_dir: Path | None = None,
    jobs: int | None = None,
    extra_cmake_defines: dict[str, str] | None = None,
) -> list[Path]:
    """Rebuild the test_board fixture firmware for one or all platforms.

    Wipes *build_dir*, runs ``cmake -S <firmware_root> -B <build_dir>``
    with ``CMAKE_BUILD_TYPE`` (and ``PLATFORM`` when *platform* is set),
    then ``cmake --build <build_dir>``.

    Args:
        firmware_root: Path to the CMake source tree (the directory
            containing the top-level ``CMakeLists.txt`` and
            ``platforms/``).
        platform: Optional single platform to build (e.g. ``stm32f103``,
            ``hc32f460``). ``None`` builds every platform discovered in
            ``<firmware_root>/platforms/``.
        build_type: ``CMAKE_BUILD_TYPE`` value. Default: ``Debug``.
        build_dir: Override the build directory location. Default:
            :func:`default_build_dir`.
        jobs: Parallel build jobs (``cmake --build -j <N>``). ``None``
            lets CMake decide.
        extra_cmake_defines: Extra ``-D`` cache variables merged into
            the configure command after the target-firmware defaults
            (e.g. ``{"CONFIG_CLI": "RTT"}``).

    Returns:
        List of ``.elf`` paths actually produced (one per platform
        built).

    Raises:
        FileNotFoundError: ``firmware_root`` is missing or doesn't look
            like a MioLink_TestSetup CMake source tree.
        BuildError: CMake configure or build returned a non-zero exit
            code, or expected ``.elf`` artefacts were not produced.
        ValueError: invalid *build_type*, unknown *platform*, or unsafe
            *build_dir*.
    """
    if build_type not in _BUILD_TYPES:
        raise ValueError(
            f"invalid build_type '{build_type}'; expected one of "
            f"{', '.join(_BUILD_TYPES)}"
        )

    firmware_root = firmware_root.resolve()
    platforms_dir = firmware_root / "platforms"
    available = _discover_platforms(platforms_dir)
    if not available:
        raise FileNotFoundError(
            f"no platforms found under {platforms_dir}; firmware_root "
            f"does not look like a MioLink_TestSetup CMake source tree"
        )

    if platform is not None and platform not in available:
        raise ValueError(
            f"unknown platform '{platform}'; available: "
            f"{', '.join(available)}"
        )

    if build_dir is None:
        build_dir = default_build_dir(firmware_root, build_type)
    build_dir = build_dir.resolve()

    defines: dict[str, str] = {"CMAKE_BUILD_TYPE": build_type}
    if platform is not None:
        defines["PLATFORM"] = platform
    if extra_cmake_defines:
        defines.update(extra_cmake_defines)

    platforms_to_build = (platform,) if platform is not None else available
    expected = [
        expected_elf(build_dir, plat) for plat in platforms_to_build
    ]

    cmake_rebuild(
        source_dir=firmware_root,
        build_dir=build_dir,
        cmake_defines=defines,
        jobs=jobs,
        expected_artefacts=expected,
    )

    for elf in expected:
        print(f"[ ok ] built {elf}")
    return expected


# ── CLI ──────────────────────────────────────────────────────────────


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
            "Rebuild the MioLink_TestSetup target firmware "
            "(test_board_<platform>) for one or all platforms."
        ),
    )
    parser.add_argument(
        "--firmware-root", type=Path, required=True,
        help=(
            "Path to the CMake source tree (the directory containing "
            "the top-level CMakeLists.txt and platforms/)."
        ),
    )
    parser.add_argument(
        "--platform", default=None,
        help=(
            "Single platform to build (e.g. stm32f103, hc32f460). "
            "Omit to build every platform discovered under "
            "<firmware_root>/platforms/."
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
            "<firmware_root>/build for Debug, "
            "<firmware_root>/build-<type_lower> otherwise."
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

    extra_defines = dict(args.cmake_define)

    try:
        elfs = build_target_firmware(
            firmware_root=args.firmware_root,
            platform=args.platform,
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

    print(f"\nBuilt {len(elfs)} target ELF(s):")
    for elf in elfs:
        print(f"  {elf}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

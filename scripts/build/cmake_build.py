"""Project-agnostic CMake rebuild helper.

Provides :func:`cmake_rebuild` — the shared core used by both
``scripts/miolink/build.py`` (MioLink probe firmware) and
``scripts/targets/build.py`` (MioLink_TestSetup target firmware). It:

  1. Validates the source tree contains a top-level ``CMakeLists.txt``.
  2. Refuses to wipe dangerous directories (build_dir == source_dir,
     or non-CMake directory outside source_dir).
  3. ``shutil.rmtree`` the build directory.
  4. Runs ``cmake -S <source> -B <build_dir>`` with caller-supplied
     ``-D`` defines + extra args.
  5. Runs ``cmake --build <build_dir>`` with the caller's ``-j``.
  6. Asserts every path in *expected_artefacts* exists under
     *build_dir* afterwards.

Process output is inherited (not captured) so progress is visible on
the terminal in real time.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


class BuildError(Exception):
    """Raised when CMake configure or build fails, or artefacts are missing."""


def cmake_rebuild(
    source_dir: Path,
    build_dir: Path,
    cmake_defines: dict[str, str] | None = None,
    jobs: int | None = None,
    expected_artefacts: list[Path] | None = None,
) -> None:
    """Wipe *build_dir* and rebuild a CMake project from scratch.

    Args:
        source_dir: Path to the CMake source tree (must contain
            ``CMakeLists.txt``).
        build_dir: Destination build directory. Wiped before configure.
        cmake_defines: Mapping of CMake cache variables forwarded as
            ``-DKEY=VALUE`` (e.g. ``{"PICO_BOARD": "miolink"}``).
        jobs: ``-j N`` for the build phase. ``None`` lets CMake decide
            (passes a bare ``-j``).
        expected_artefacts: Paths that MUST exist after the build. Each
            may be absolute or relative to *build_dir*. Missing entries
            raise :class:`BuildError`.

    Raises:
        FileNotFoundError: *source_dir* has no ``CMakeLists.txt``.
        ValueError: *build_dir* is unsafe to wipe (equals source, or is
            outside source_dir and not a CMake build).
        BuildError: configure or build exited non-zero, or an expected
            artefact is missing.
    """
    source_dir = source_dir.resolve()
    if not (source_dir / "CMakeLists.txt").is_file():
        raise FileNotFoundError(
            f"source_dir does not contain CMakeLists.txt: {source_dir}"
        )

    build_dir = build_dir.resolve()

    if build_dir == source_dir:
        raise ValueError(
            f"build_dir must not equal source_dir: {build_dir}"
        )

    # Refuse to wipe anything that is not clearly a CMake build dir
    # under source_dir. Prevents accidental damage when build_dir is
    # mis-specified.
    if (
        build_dir.exists()
        and source_dir not in build_dir.parents
    ):
        looks_like_cmake = (
            (build_dir / "CMakeCache.txt").is_file()
            or (build_dir / "CMakeFiles").is_dir()
            or not any(build_dir.iterdir())
        )
        if not looks_like_cmake:
            raise ValueError(
                f"refusing to wipe {build_dir}: not inside source_dir "
                f"and does not look like a CMake build directory"
            )

    if build_dir.exists():
        print(f"[step] removing existing build dir {build_dir} ...")
        shutil.rmtree(build_dir)

    summary = ", ".join(
        f"{k}={v}" for k, v in (cmake_defines or {}).items()
    ) or "(no defines)"
    print(f"[step] configuring CMake [{summary}] ...")

    cmake_cmd = [
        "cmake", "-S", str(source_dir), "-B", str(build_dir),
    ]
    for key, value in (cmake_defines or {}).items():
        cmake_cmd.append(f"-D{key}={value}")

    result = subprocess.run(cmake_cmd)
    if result.returncode != 0:
        raise BuildError(
            f"cmake configure failed (exit code {result.returncode}); "
            f"command: {' '.join(cmake_cmd)}"
        )

    print(f"[step] building ...")
    build_cmd = ["cmake", "--build", str(build_dir)]
    if jobs is None:
        build_cmd.append("-j")
    else:
        build_cmd += ["-j", str(jobs)]

    result = subprocess.run(build_cmd)
    if result.returncode != 0:
        raise BuildError(
            f"cmake build failed (exit code {result.returncode}); "
            f"command: {' '.join(build_cmd)}"
        )

    missing: list[Path] = []
    for artefact in expected_artefacts or []:
        path = artefact if artefact.is_absolute() else (build_dir / artefact)
        if not path.is_file():
            missing.append(path)
    if missing:
        raise BuildError(
            "build completed but expected artefact(s) missing:\n  "
            + "\n  ".join(str(p) for p in missing)
        )


def positive_int(value: str) -> int:
    """argparse helper: parse a strictly-positive integer (hex/dec)."""
    try:
        n = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {n}"
        )
    return n

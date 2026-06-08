"""Shared CMake-build helpers.

Modules:
  - cmake_build: project-agnostic ``cmake_rebuild`` driver — wipe a
    build directory, run ``cmake`` configure, run ``cmake --build``,
    and assert expected artefacts exist.

Public symbols are re-exported here so callers can write
`from cmake import cmake_rebuild` or
`import cmake; cmake.cmake_rebuild(...)`.
"""

from .cmake_build import (
    BuildError,
    cmake_rebuild,
    positive_int,
)

__all__ = [
    "BuildError",
    "cmake_rebuild",
    "positive_int",
]

"""MioLink_TestSetup target-firmware build helpers.

Builds the ``test_board_<platform>`` fixture firmware that the bench
runner loads onto each target MCU. Use this whenever you need fresh
``.elf`` files in ``build/test_boards/test_board_<platform>/`` (e.g.
before invoking ``scripts/bench/run_bench.py``).

Modules:
  - build: rebuild one or all target platforms (wipe + cmake + build).

Public symbols of each module are re-exported here so callers can write
`from targets import build_target_firmware` or
`import targets; targets.build_target_firmware(...)`.
"""

from .build import (
    build_target_firmware,
    default_build_dir,
)

__all__ = [
    "build_target_firmware",
    "default_build_dir",
]

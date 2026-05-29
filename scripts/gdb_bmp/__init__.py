"""GDB/Black Magic Probe helper package.

Modules:
  - session: GDB/MI session management against a Black Magic Probe
    (connect, scan, attach, monitor, frequency/Vtref handling).
  - target_flash: load/verify an ELF firmware image onto an attached target.

Public symbols of both modules are re-exported here so callers can write
`from gdb_bmp import BmpSession, flash_target` or
`import gdb_bmp; gdb_bmp.flash_target(...)`.
"""

from .session import (
    BmpConnectionError,
    BmpError,
    BmpSession,
    BmpTargetConfig,
    FrequencyError,
    ScanError,
    ScanInterface,
    ScannedTarget,
    TargetMismatchError,
    VtrefError,
    add_target_arguments,
    check_responses,
    config_from_args,
    console_lines,
    parse_frequency,
)
from .target_flash import (
    BmpFlashConfig,
    FlashError,
    VerifyError,
    flash_target,
)

__all__ = [
    "BmpConnectionError",
    "BmpError",
    "BmpFlashConfig",
    "BmpSession",
    "BmpTargetConfig",
    "FlashError",
    "FrequencyError",
    "ScanError",
    "ScanInterface",
    "ScannedTarget",
    "TargetMismatchError",
    "VerifyError",
    "VtrefError",
    "add_target_arguments",
    "check_responses",
    "config_from_args",
    "console_lines",
    "flash_target",
    "parse_frequency",
]

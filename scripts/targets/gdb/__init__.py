"""GDB/Black Magic Probe helper package.

Modules:
  - session: GDB/MI session management against a Black Magic Probe
    (connect, scan, attach, monitor, frequency/Vtref handling).
  - load: load/verify an ELF firmware image onto an attached target.

Public symbols of both modules are re-exported here so callers can write
`from targets.gdb import BmpSession, flash_target` or
`from targets import gdb; gdb.flash_target(...)`.
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
from .load import (
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

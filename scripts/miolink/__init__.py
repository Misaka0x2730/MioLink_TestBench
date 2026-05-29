"""MioLink-specific helpers.

Modules:
  - build: rebuild the MioLink firmware from scratch (wipe + cmake + build).
  - flash: flash a .uf2 firmware image onto a MioLink probe via DFU+UF2.
  - find_ports: discover the GDB and UART serial ports of an enumerated MioLink.

Public symbols of each module are re-exported here so callers can write
`from miolink import flash_miolink, find_serial_ports` or
`import miolink; miolink.flash_miolink(...)`.
"""

from .build import (
    build_miolink_firmware,
    default_build_dir,
)
from .flash import (
    MIOLINK_PID,
    MIOLINK_VID,
    flash_miolink,
)
from .find_ports import (
    MioLinkPorts,
    PortNotFoundError,
    find_serial_ports,
)

__all__ = [
    "MIOLINK_PID",
    "MIOLINK_VID",
    "MioLinkPorts",
    "PortNotFoundError",
    "build_miolink_firmware",
    "default_build_dir",
    "find_serial_ports",
    "flash_miolink",
]

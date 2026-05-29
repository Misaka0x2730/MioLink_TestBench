"""DFU detach module.

Sends a DFU_DETACH request to a USB device identified by its bus address,
causing it to transition from Runtime mode to DFU mode.

Usage as module:
    from usb_helpers.find_device import find_device_address
    from dfu.detach import dfu_detach

    addr = find_device_address(vid=0x1D50, pid=0x6018, serial="XXXX")
    dfu_detach(addr)

Usage as CLI:
    python dfu/detach.py --path 1-1.3.4
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Allow `from usb_helpers.find_device import ...` whether this file is
# run as a standalone CLI or imported as `dfu.detach` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usb_helpers.find_device import UsbDeviceAddress


class DfuUtilNotFoundError(Exception):
    """Raised when dfu-util binary is not found in PATH."""


class DfuDetachError(Exception):
    """Raised when dfu-util detach command fails."""

    def __init__(self, message: str, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def _find_dfu_util() -> str:
    """Locate the dfu-util binary."""
    path = shutil.which("dfu-util")
    if path is None:
        raise DfuUtilNotFoundError(
            "dfu-util not found in PATH. "
            "Install it with: brew install dfu-util (macOS) / "
            "apt install dfu-util (Linux) / "
            "download from https://dfu-util.sourceforge.net (Windows)"
        )
    return path


def dfu_detach(address: UsbDeviceAddress) -> None:
    """Send DFU_DETACH request to a device at the given bus address.

    The device must expose a DFU Runtime interface. After a successful
    detach the device will re-enumerate in DFU mode.

    Args:
        address: USB device address obtained from find_device_address().

    Raises:
        DfuUtilNotFoundError: dfu-util binary is not installed.
        DfuDetachError: The detach command failed.
        ValueError: port_path is not available on the address.
    """
    dfu_util = _find_dfu_util()

    # dfu-util has --path (bus+ports) and --devnum (devnum from --list) but
    # NO --bus filter. Falling back to --devnum without a bus filter is
    # ambiguous on hosts with multiple USB controllers, where two devices
    # may share the same devnum on different buses. Require port_path,
    # which is always populated by find_device_address().
    if not address.port_path:
        raise ValueError(
            "UsbDeviceAddress.port_path is required for dfu-util detach. "
            "Construct the address via find_device_address(), which fills "
            "port_numbers from libusb."
        )

    cmd = [dfu_util, "--path", address.port_path, "--detach"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return

    stderr = result.stderr.strip()

    # Older MioLink firmware advertises a DFU Runtime interface without
    # the optional DFU functional descriptor; dfu-util then exits 74
    # ("USB error") after the detach packet has already left and the
    # device is gone from the bus. Treat this as success: the absence
    # of the device IS the post-detach state we wanted.
    if (
        result.returncode == 74
        and "LIBUSB_ERROR_NO_DEVICE" in stderr
    ):
        return

    raise DfuDetachError(
        f"dfu-util detach failed (exit code {result.returncode}): {stderr}",
        returncode=result.returncode,
        stderr=stderr,
    )


def _parse_path(value: str) -> str:
    """Validate a USB port path string (e.g. '1-1.3.4')."""
    if "-" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid port path '{value}'. Expected format: BUS-PORT.PORT... "
            f"(e.g. 1-1.3.4)"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send DFU_DETACH to a USB device by its bus port path.",
    )

    parser.add_argument(
        "--path",
        required=True,
        type=_parse_path,
        help="USB port path (e.g. 1-1.3.4)",
    )

    args = parser.parse_args()

    bus_str, ports_str = args.path.split("-", 1)
    address = UsbDeviceAddress(
        bus=int(bus_str),
        address=0,
        port_numbers=tuple(int(p) for p in ports_str.split(".")),
    )

    try:
        dfu_detach(address)
    except (DfuUtilNotFoundError, DfuDetachError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"DFU detach sent to device at {address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

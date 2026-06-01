"""picotool-based firmware loader.

Loads a firmware image (.uf2/.elf/.bin) onto an RP2040/RP2350 device that
is already in BOOTSEL mode, using ``picotool`` over the PICOBOOT vendor
interface.

Why this instead of uf2/upload.py: the USB-MSC drag-and-drop path writes
the .uf2 to a FAT volume and the RP2 bootrom reboots once it has counted
all UF2 blocks, but the transfer is unacknowledged. If a single MSC block
write is dropped/reordered under USB-hub contention the bootrom never
reaches a complete image, never reboots, and silently stays in BOOTSEL
while the host's copy reports success. picotool performs acknowledged,
verifiable writes and deterministically reboots into the freshly flashed
application, which removes that failure mode (observed mostly on RP2350 /
Pico 2W, but the mechanism is common to all RP2 boards).

The device must already be in BOOTSEL (e.g. after dfu/detach.py). This
module does NOT reset a running device into BOOTSEL: MioLink exposes a
DFU-runtime detach, not picotool's reset interface, so picotool's
``--force`` is intentionally not used.

Usage as module:
    from usb_helpers.find_device import find_device_address
    from dfu.detach import dfu_detach
    from picotool.load import picotool_load

    addr = find_device_address(vid=0x1D50, pid=0x6018, serial="XXXX")
    dfu_detach(addr)                 # running app -> BOOTSEL
    picotool_load(addr, "firmware.uf2")

Usage as CLI:
    python picotool/load.py --path 1-1.3.4 firmware.uf2
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Allow `from usb_helpers.find_device import ...` whether this file is
# run as a standalone CLI or imported as `picotool.load` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usb_helpers.find_device import (
    DeviceNotFoundError,
    MultipleDevicesFoundError,
    UsbDeviceAddress,
    find_device_address,
)

# RP2040/RP2350 ROM bootloader (BOOTSEL) USB identity, shared by every RP2
# chip. picotool talks to its PICOBOOT vendor interface; the MSC interface
# (if mounted by the host) is irrelevant to this path.
_RP2_BOOTSEL_VID = 0x2E8A
_RP2_BOOTSEL_PID = 0x0003


class PicotoolNotFoundError(Exception):
    """Raised when the picotool binary is not found in PATH."""


class BootselDeviceNotFoundError(Exception):
    """Raised when no RP2 BOOTSEL device appears at the expected port."""


class PicotoolLoadError(Exception):
    """Raised when the picotool load command fails."""

    def __init__(self, message: str, returncode: int, stderr: str) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr


def _find_picotool() -> str:
    """Locate the picotool binary."""
    path = shutil.which("picotool")
    if path is None:
        raise PicotoolNotFoundError(
            "picotool not found in PATH. Install it with: "
            "brew install picotool (macOS) / "
            "apt install picotool (Raspberry Pi OS / Debian) / "
            "build from https://github.com/raspberrypi/picotool. "
            "RP2350 support requires picotool >= 2.0."
        )
    return path


def _find_bootsel_address(port_path: str) -> UsbDeviceAddress | None:
    """Return the current address of the BOOTSEL device at *port_path*.

    The BOOTSEL device re-enumerates with a fresh USB device address after
    a DFU detach, but its physical ``port_path`` is stable. We resolve the
    current bus/address by that port so picotool can target exactly this
    device even if another probe is stuck in BOOTSEL on a different port.

    Returns None when no BOOTSEL device is present at *port_path*.
    """
    try:
        candidates = [find_device_address(_RP2_BOOTSEL_VID, _RP2_BOOTSEL_PID)]
    except MultipleDevicesFoundError as exc:
        candidates = exc.addresses
    except DeviceNotFoundError:
        return None

    for candidate in candidates:
        if candidate.port_path == port_path:
            return candidate
    return None


def _wait_for_bootsel(
    port_path: str,
    timeout_sec: float,
    poll_interval_sec: float = 0.5,
) -> UsbDeviceAddress | None:
    """Poll until a BOOTSEL device appears at *port_path*, or time out."""
    deadline = time.monotonic() + timeout_sec
    while True:
        address = _find_bootsel_address(port_path)
        if address is not None:
            return address
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval_sec)


def picotool_load(
    address: UsbDeviceAddress,
    uf2_path: str | Path,
    bootsel_timeout_sec: float = 30.0,
    attempts: int = 3,
    verify: bool = True,
    execute: bool = True,
) -> None:
    """Flash *uf2_path* onto the BOOTSEL device sharing *address*'s port.

    *address* is the address of the probe BEFORE detach; only its stable
    ``port_path`` is used to locate the post-detach BOOTSEL device.

    Args:
        address: USB address whose port_path identifies the target probe.
        uf2_path: Path to the firmware image (.uf2/.elf/.bin).
        bootsel_timeout_sec: Max seconds to wait for the BOOTSEL device to
                             appear after detach (first attempt).
        attempts: Number of picotool load attempts before giving up. A
                  failed load leaves the device in BOOTSEL, so retrying is
                  safe and idempotent.
        verify: Pass ``-v`` to verify the written data.
        execute: Pass ``-x`` to reboot into the application after loading.

    Raises:
        PicotoolNotFoundError: picotool binary is not installed.
        FileNotFoundError: The firmware file does not exist.
        ValueError: address has no usable port_path.
        BootselDeviceNotFoundError: No BOOTSEL device appeared in time.
        PicotoolLoadError: Every picotool load attempt failed.
    """
    picotool = _find_picotool()

    uf2_path = Path(uf2_path)
    if not uf2_path.is_file():
        raise FileNotFoundError(f"firmware file not found: {uf2_path}")

    target_port = address.port_path
    if not target_port:
        raise ValueError(
            "UsbDeviceAddress.port_path is required to target the BOOTSEL "
            "device. Construct the address via find_device_address()."
        )

    last_error: PicotoolLoadError | None = None
    load_attempted = False

    for attempt in range(1, attempts + 1):
        # On the first attempt give the device time to re-enumerate into
        # BOOTSEL after detach; on retries it should still be there because
        # a failed load does not reboot it.
        wait_sec = bootsel_timeout_sec if attempt == 1 else 5.0
        bootsel = _wait_for_bootsel(target_port, wait_sec)

        if bootsel is None:
            if load_attempted:
                # A previous attempt already rebooted the device out of
                # BOOTSEL: the load took. Treat the missing BOOTSEL device
                # as success.
                return
            raise BootselDeviceNotFoundError(
                f"no RP2 BOOTSEL device (VID=0x{_RP2_BOOTSEL_VID:04X}, "
                f"PID=0x{_RP2_BOOTSEL_PID:04X}) at port {target_port} "
                f"within {bootsel_timeout_sec:.0f}s after detach"
            )

        cmd = [picotool, "load"]
        if verify:
            cmd.append("-v")
        if execute:
            cmd.append("-x")
        cmd += [
            "--bus", str(bootsel.bus),
            "--address", str(bootsel.address),
            str(uf2_path),
        ]

        load_attempted = True
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return

        last_error = PicotoolLoadError(
            f"picotool load failed (exit code {result.returncode}) on "
            f"attempt {attempt}/{attempts}: {result.stderr.strip()}",
            returncode=result.returncode,
            stderr=result.stderr.strip(),
        )
        # Failed load leaves the device in BOOTSEL; pause and retry.
        time.sleep(1.0)

    assert last_error is not None
    raise last_error


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
        description=(
            "Flash a firmware image onto an RP2 device in BOOTSEL mode at a "
            "given USB port path, using picotool."
        ),
    )
    parser.add_argument(
        "--path", required=True, type=_parse_path,
        help="USB port path of the probe (e.g. 1-1.3.4)",
    )
    parser.add_argument(
        "uf2_file", type=Path,
        help="Path to the firmware image (.uf2/.elf/.bin)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Seconds to wait for the BOOTSEL device (default: 30)",
    )
    parser.add_argument(
        "--attempts", type=int, default=3,
        help="picotool load attempts before failing (default: 3)",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip picotool's post-write verify (-v).",
    )
    parser.add_argument(
        "--no-execute", action="store_true",
        help="Do not reboot into the application after loading (-x).",
    )

    args = parser.parse_args()

    bus_str, ports_str = args.path.split("-", 1)
    address = UsbDeviceAddress(
        bus=int(bus_str),
        address=0,
        port_numbers=tuple(int(p) for p in ports_str.split(".")),
    )

    try:
        picotool_load(
            address,
            args.uf2_file,
            bootsel_timeout_sec=args.timeout,
            attempts=args.attempts,
            verify=not args.no_verify,
            execute=not args.no_execute,
        )
    except (
        PicotoolNotFoundError, FileNotFoundError, ValueError,
        BootselDeviceNotFoundError, PicotoolLoadError,
    ) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Successfully loaded {args.uf2_file.name} via picotool at {address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

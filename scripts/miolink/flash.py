"""MioLink firmware flashing script.

Flashes a .uf2 firmware file to a MioLink device identified by its
USB serial number. Orchestrates the full pipeline:
  1. Find MioLink on the bus by serial number
  2. Send DFU_DETACH (device resets into RP2040/RP2350 BOOTSEL bootloader)
  3. Load the .uf2 via picotool over PICOBOOT (acknowledged + verified
     writes, deterministic reboot into the new firmware)

The .uf2 is intentionally NOT copied to the BOOTSEL MSC volume: that
transfer is unacknowledged and an occasional dropped USB block leaves the
device silently stuck in BOOTSEL (see picotool_load.py for details).

Usage as module:
    from miolink import flash_miolink
    flash_miolink(serial="E661640843699535", uf2_path="firmware.uf2")

Usage as CLI:
    python miolink/flash.py --serial E661640843699535 firmware.uf2
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow sibling-package imports (`usbutil`, `miolink`) whether this
# file is run as a standalone CLI or imported as `miolink.flash` from
# another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usbutil.find_device import (
    DeviceNotFoundError,
    find_device_address,
)
from miolink.dfu_detach import (
    DfuDetachError,
    DfuUtilNotFoundError,
    dfu_detach,
)
from miolink.picotool_load import (
    BootselDeviceNotFoundError,
    PicotoolLoadError,
    PicotoolNotFoundError,
    picotool_load,
)

MIOLINK_VID = 0x1D50
MIOLINK_PID = 0x6018

# ── UF2 file validation ──────────────────────────────────────────────
# Self-contained here: the probe is flashed via picotool over PICOBOOT,
# not the USB-MSC drag-and-drop path, so only this lightweight format
# check is needed (the old uf2/ MSC uploader was unused and removed).

_UF2_MAGIC_0 = 0x0A324655  # "UF2\n"
_UF2_MAGIC_1 = 0x9E5D5157
_UF2_BLOCK_SIZE = 512


class Uf2ValidationError(Exception):
    """Raised when the file is not a valid UF2."""


def validate_uf2(path: Path) -> None:
    """Verify the file exists and has valid UF2 header magic bytes."""
    if not path.exists():
        raise FileNotFoundError(f"UF2 file not found: {path}")

    if path.suffix.lower() != ".uf2":
        raise Uf2ValidationError(f"Expected .uf2 extension: {path.name}")

    size = path.stat().st_size
    if size < _UF2_BLOCK_SIZE:
        raise Uf2ValidationError(
            f"File too small ({size} bytes, min {_UF2_BLOCK_SIZE}): {path.name}"
        )

    with open(path, "rb") as f:
        header = f.read(8)

    magic0 = int.from_bytes(header[0:4], "little")
    magic1 = int.from_bytes(header[4:8], "little")
    if magic0 != _UF2_MAGIC_0 or magic1 != _UF2_MAGIC_1:
        raise Uf2ValidationError(
            f"Invalid UF2 magic in {path.name}: "
            f"0x{magic0:08X}:0x{magic1:08X}, "
            f"expected 0x{_UF2_MAGIC_0:08X}:0x{_UF2_MAGIC_1:08X}"
        )


def flash_miolink(
    serial: str,
    uf2_path: str | Path,
    bootsel_timeout_sec: float = 30.0,
) -> None:
    """Flash a MioLink device with a .uf2 firmware file.

    Args:
        serial: USB serial number of the target MioLink.
        uf2_path: Path to the .uf2 firmware file.
        bootsel_timeout_sec: Max seconds to wait for the BOOTSEL device
                             to appear after DFU detach.

    Raises:
        FileNotFoundError: The .uf2 file does not exist.
        Uf2ValidationError: The file is not a valid UF2 file.
        DeviceNotFoundError: MioLink with given serial not found.
        DfuUtilNotFoundError: dfu-util is not installed.
        DfuDetachError: DFU detach command failed.
        PicotoolNotFoundError: picotool is not installed.
        BootselDeviceNotFoundError: BOOTSEL device did not appear in time.
        PicotoolLoadError: picotool failed to load the firmware.
    """
    uf2_path = Path(uf2_path)
    validate_uf2(uf2_path)

    address = find_device_address(
        vid=MIOLINK_VID, pid=MIOLINK_PID, serial=serial,
    )

    dfu_detach(address)
    picotool_load(address, uf2_path, bootsel_timeout_sec=bootsel_timeout_sec)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash a MioLink device with a .uf2 firmware file.",
    )
    parser.add_argument(
        "--serial", required=True,
        help="USB serial number of the target MioLink",
    )
    parser.add_argument(
        "uf2_file", type=Path,
        help="Path to the .uf2 firmware file",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Seconds to wait for the BOOTSEL device after DFU detach (default: 30)",
    )

    args = parser.parse_args()

    try:
        flash_miolink(args.serial, args.uf2_file, args.timeout)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Uf2ValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except DeviceNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except DfuUtilNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except DfuDetachError as e:
        print(f"Error: DFU detach failed: {e}", file=sys.stderr)
        return 1
    except PicotoolNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except BootselDeviceNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except PicotoolLoadError as e:
        print(f"Error: picotool load failed: {e}", file=sys.stderr)
        return 1

    print(f"Successfully flashed {args.uf2_file.name} to MioLink ({args.serial})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

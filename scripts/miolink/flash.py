"""MioLink firmware flashing script.

Flashes a .uf2 firmware file to a MioLink device identified by its
USB serial number. Orchestrates the full pipeline:
  1. Find MioLink on the bus by serial number
  2. Send DFU_DETACH (device resets into RP2040 bootloader / USB MSC)
  3. Upload .uf2 to the MSC volume (device reboots with new firmware)

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

# Allow sibling-package imports (`usb_helpers`, `dfu`, `uf2`) whether this
# file is run as a standalone CLI or imported as `miolink_flash.flash` from
# another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usb_helpers.find_device import (
    DeviceNotFoundError,
    find_device_address,
)
from dfu.detach import (
    DfuDetachError,
    DfuUtilNotFoundError,
    dfu_detach,
)
from uf2.upload import (
    MscMountNotFoundError,
    Uf2UploadError,
    Uf2ValidationError,
    upload_uf2,
    validate_uf2,
)

MIOLINK_VID = 0x1D50
MIOLINK_PID = 0x6018


def flash_miolink(
    serial: str,
    uf2_path: str | Path,
    msc_timeout_sec: float = 30.0,
) -> None:
    """Flash a MioLink device with a .uf2 firmware file.

    Args:
        serial: USB serial number of the target MioLink.
        uf2_path: Path to the .uf2 firmware file.
        msc_timeout_sec: Max seconds to wait for the MSC volume
                         to appear after DFU detach.

    Raises:
        FileNotFoundError: The .uf2 file does not exist.
        Uf2ValidationError: The file is not a valid UF2 file.
        DeviceNotFoundError: MioLink with given serial not found.
        DfuUtilNotFoundError: dfu-util is not installed.
        DfuDetachError: DFU detach command failed.
        MscMountNotFoundError: MSC volume did not appear in time.
        Uf2UploadError: File copy to MSC volume failed.
    """
    uf2_path = Path(uf2_path)
    validate_uf2(uf2_path)

    address = find_device_address(
        vid=MIOLINK_VID, pid=MIOLINK_PID, serial=serial,
    )

    dfu_detach(address)
    upload_uf2(address, uf2_path, timeout_sec=msc_timeout_sec)


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
        help="Seconds to wait for MSC volume after DFU detach (default: 30)",
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
    except MscMountNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Uf2UploadError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Successfully flashed {args.uf2_file.name} to MioLink ({args.serial})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

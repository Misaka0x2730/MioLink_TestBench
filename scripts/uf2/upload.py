"""UF2 firmware uploader for USB MSC devices.

Uploads a .uf2 firmware file to a USB device in Mass Storage Class mode,
identified by its bus address obtained from usb_helpers.find_device.

Usage as module:
    from usb_helpers.find_device import find_device_address
    from uf2.upload import upload_uf2

    addr = find_device_address(vid=0x2E8A, pid=0x0003)
    upload_uf2(addr, "firmware.uf2")

Usage as CLI:
    python uf2/upload.py --path 1-1.3.4 firmware.uf2
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Allow `from usb_helpers.find_device import ...` whether this file is
# run as a standalone CLI or imported as `uf2.upload` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usb_helpers.find_device import UsbDeviceAddress

_PLATFORM = platform.system()

_UF2_MAGIC_0 = 0x0A324655  # "UF2\n"
_UF2_MAGIC_1 = 0x9E5D5157
_UF2_BLOCK_SIZE = 512


class MscMountNotFoundError(Exception):
    """Raised when the MSC mount point cannot be found."""


class Uf2UploadError(Exception):
    """Raised when the UF2 file upload fails."""


class Uf2ValidationError(Exception):
    """Raised when the file is not a valid UF2."""


# ── Public API ───────────────────────────────────────────────────────


def upload_uf2(
    address: UsbDeviceAddress,
    uf2_path: str | Path,
    timeout_sec: float = 30.0,
) -> None:
    """Upload a .uf2 file to a USB MSC device.

    Args:
        address: USB device bus address (from find_device_address).
        uf2_path: Path to the .uf2 firmware file.
        timeout_sec: Max seconds to wait for the MSC volume to appear.

    Raises:
        FileNotFoundError: The .uf2 file does not exist.
        Uf2ValidationError: The file is not a valid UF2 file.
        MscMountNotFoundError: MSC volume not found within timeout.
        Uf2UploadError: File copy failed.
    """
    uf2_path = Path(uf2_path)
    validate_uf2(uf2_path)

    mount_point = find_msc_mount_point(address, timeout_sec)
    dest = mount_point / uf2_path.name

    try:
        shutil.copy(uf2_path, dest)
    except OSError as e:
        raise Uf2UploadError(
            f"Failed to copy {uf2_path.name} to {mount_point}: {e}"
        )

    try:
        os.sync()
    except OSError:
        pass


def find_msc_mount_point(
    address: UsbDeviceAddress,
    timeout_sec: float = 30.0,
) -> Path:
    """Find the mount point of a USB MSC device, polling until available.

    Args:
        address: USB device bus address.
        timeout_sec: Max seconds to wait for the volume to appear.

    Returns:
        Path to the mounted volume.

    Raises:
        MscMountNotFoundError: Volume not found within timeout.
    """
    if _PLATFORM == "Darwin":
        probe = _find_mount_darwin
    elif _PLATFORM == "Linux":
        probe = _find_mount_linux
    else:
        raise NotImplementedError(f"Unsupported platform: {_PLATFORM}")

    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None

    while True:
        try:
            return probe(address)
        except MscMountNotFoundError as e:
            last_error = e

        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    raise MscMountNotFoundError(
        f"MSC volume for device at {address} not found "
        f"within {timeout_sec:.0f}s: {last_error}"
    )


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


# ── macOS ────────────────────────────────────────────────────────────


def _find_mount_darwin(address: UsbDeviceAddress) -> Path:
    """Find MSC mount point on macOS via ioreg + diskutil."""
    target = address.port_path
    if not target:
        raise MscMountNotFoundError("port_path is required on macOS")

    usb_disk_map = _ioreg_usb_to_bsd_names()
    for bsd_name in usb_disk_map.get(target, []):
        mount = _bsd_name_to_mount_darwin(bsd_name)
        if mount is not None:
            return mount

    raise MscMountNotFoundError(
        f"No mounted MSC volume found for device at {target}"
    )


def _ioreg_usb_to_bsd_names() -> dict[str, list[str]]:
    """Map USB port paths to BSD disk names by parsing ioreg output.

    Scans IOUSBHostDevice subtrees for locationID → BSD Name pairs.
    locationID is converted to a port_path string for matching.
    """
    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}

    if result.returncode != 0:
        return {}

    mapping: dict[str, list[str]] = {}
    current_path: str | None = None

    for line in result.stdout.splitlines():
        loc_match = re.search(r'"locationID"\s*=\s*(\d+)', line)
        if loc_match:
            loc_id = int(loc_match.group(1))
            current_path = _location_id_to_port_path(loc_id)
            mapping.setdefault(current_path, [])

        if current_path is not None:
            bsd_match = re.search(r'"BSD Name"\s*=\s*"(disk\w*)"', line)
            if bsd_match:
                name = bsd_match.group(1)
                if name not in mapping[current_path]:
                    mapping[current_path].append(name)

    return mapping


def _location_id_to_port_path(location_id: int) -> str:
    """Convert macOS IOKit locationID to USB port path.

    locationID encodes the bus in the top byte and port numbers
    in subsequent nibbles (4 bits each), zero-terminated.
    Example: 0x01134000 → bus=1, ports=[1,3,4] → "1-1.3.4"
    """
    bus = (location_id >> 24) & 0xFF
    ports: list[int] = []
    for shift in range(20, 0, -4):
        port = (location_id >> shift) & 0xF
        if port == 0:
            break
        ports.append(port)

    if not ports:
        return str(bus)
    return f"{bus}-" + ".".join(str(p) for p in ports)


def _bsd_name_to_mount_darwin(bsd_name: str) -> Path | None:
    """Get mount point for a BSD disk name, checking the disk and partition."""
    for suffix in ("s1", ""):
        dev = f"/dev/{bsd_name}{suffix}"
        try:
            result = subprocess.run(
                ["diskutil", "info", "-plist", dev],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        if result.returncode != 0:
            continue

        try:
            info = plistlib.loads(result.stdout.encode())
        except Exception:
            continue

        mp = info.get("MountPoint")
        if mp:
            path = Path(mp)
            if path.is_dir():
                return path

    return None


# ── Linux ────────────────────────────────────────────────────────────


def _find_mount_linux(address: UsbDeviceAddress) -> Path:
    """Find MSC mount point on Linux via sysfs + findmnt."""
    port_path = address.port_path
    if not port_path:
        raise MscMountNotFoundError("port_path is required on Linux")

    sysfs_dev = Path(f"/sys/bus/usb/devices/{port_path}")
    if not sysfs_dev.exists():
        raise MscMountNotFoundError(f"USB device not in sysfs: {sysfs_dev}")

    block_dirs = sorted(sysfs_dev.glob("*/host*/target*/*/*/block/*"))
    if not block_dirs:
        raise MscMountNotFoundError(
            f"No block device found for USB device at {port_path}"
        )

    block_name = block_dirs[0].name

    candidates = sorted(
        Path(f"/sys/block/{block_name}").glob(f"{block_name}[0-9]*"),
        key=lambda p: p.name,
    )
    dev_nodes = [f"/dev/{p.name}" for p in candidates]
    dev_nodes.append(f"/dev/{block_name}")

    for dev_node in dev_nodes:
        try:
            result = subprocess.run(
                ["findmnt", "-n", "-o", "TARGET", dev_node],
                capture_output=True, text=True, timeout=5,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

        target = result.stdout.strip()
        if target:
            path = Path(target)
            if path.is_dir():
                return path

    raise MscMountNotFoundError(
        f"Block device /dev/{block_name} exists but is not mounted"
    )


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_port_path(value: str) -> str:
    if "-" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid port path '{value}'. "
            f"Expected BUS-PORT.PORT... (e.g. 1-1.3.4)"
        )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Upload a .uf2 firmware file to a USB MSC device.",
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--path", type=_parse_port_path,
        help="USB port path (e.g. 1-1.3.4)",
    )
    target.add_argument(
        "--bus-address", nargs=2, type=int,
        metavar=("BUS", "ADDRESS"),
        help="USB bus number and device address",
    )

    parser.add_argument(
        "uf2_file", type=Path,
        help="Path to the .uf2 firmware file",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0,
        help="Seconds to wait for MSC volume (default: 30)",
    )

    args = parser.parse_args()

    if args.path:
        bus_str, ports_str = args.path.split("-", 1)
        address = UsbDeviceAddress(
            bus=int(bus_str), address=0,
            port_numbers=tuple(int(p) for p in ports_str.split(".")),
        )
    else:
        bus, addr = args.bus_address
        address = UsbDeviceAddress(bus=bus, address=addr)

    try:
        upload_uf2(address, args.uf2_file, args.timeout)
    except (
        FileNotFoundError, Uf2ValidationError,
        MscMountNotFoundError, Uf2UploadError,
    ) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Successfully uploaded {args.uf2_file.name} to device at {address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

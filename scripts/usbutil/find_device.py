"""USB device locator module.

Finds a USB device by VID, PID, and serial number descriptor,
returns its bus address for further programmatic use.

Usage as module:
    from usbutil.find_device import find_device_address
    addr = find_device_address(vid=0x1D50, pid=0x6018, serial="XXXX")

Usage as CLI:
    python usbutil/find_device.py --vid 0x1D50 --pid 0x6018 --serial XXXX
"""

from __future__ import annotations

import argparse
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import usb.core
import usb.backend.libusb1
import usb.util

_PLATFORM = platform.system()

_LIBUSB_FALLBACK_PATHS: dict[str, tuple[Path, ...]] = {
    "Darwin": (
        Path("/opt/homebrew/lib/libusb-1.0.dylib"),
        Path("/usr/local/lib/libusb-1.0.dylib"),
    ),
    "Windows": (
        Path(r"C:\Windows\System32\libusb-1.0.dll"),
        Path(r"C:\msys64\ucrt64\bin\libusb-1.0.dll"),
        Path(r"C:\msys64\mingw64\bin\libusb-1.0.dll"),
    ),
}

_INSTALL_HINTS: dict[str, str] = {
    "Darwin": "brew install libusb",
    "Linux": "apt install libusb-1.0-0  (or equivalent for your distro)",
    "Windows": "pip install libusb-package  (or install libusb from https://libusb.info)",
}


def _try_libusb_package() -> usb.backend.libusb1._LibUSB | None:
    """Try to use the 'libusb-package' pip package (bundles libusb DLL for Windows)."""
    try:
        import libusb_package
        return usb.backend.libusb1.get_backend(
            find_library=libusb_package.find_library
        )
    except ImportError:
        return None


def _try_fallback_paths() -> usb.backend.libusb1._LibUSB | None:
    """Try platform-specific fallback paths for libusb."""
    paths = _LIBUSB_FALLBACK_PATHS.get(_PLATFORM, ())
    for path in paths:
        if path.exists():
            lib_path = str(path)
            backend = usb.backend.libusb1.get_backend(
                find_library=lambda _candidate, _p=lib_path: _p
            )
            if backend is not None:
                return backend
    return None


def _get_usb_backend() -> usb.backend.libusb1._LibUSB:
    """Get a libusb1 backend with platform-specific fallbacks.

    Discovery order:
      1. Default pyusb/ctypes discovery (works on Linux, and anywhere
         libusb is on the standard library path).
      2. 'libusb-package' pip package (bundles DLL, primarily for Windows).
      3. Platform-specific fallback paths (Homebrew on macOS, MSYS2 on Windows).
    """
    backend = usb.backend.libusb1.get_backend()
    if backend is not None:
        return backend

    backend = _try_libusb_package()
    if backend is not None:
        return backend

    backend = _try_fallback_paths()
    if backend is not None:
        return backend

    hint = _INSTALL_HINTS.get(_PLATFORM, "install libusb for your platform")
    raise RuntimeError(f"libusb backend not found. Install it with: {hint}")


class DeviceNotFoundError(Exception):
    """Raised when the requested USB device cannot be found on the bus."""


class MultipleDevicesFoundError(Exception):
    """Raised when multiple matching devices are found without a serial to disambiguate."""

    def __init__(self, message: str, addresses: list[UsbDeviceAddress]) -> None:
        super().__init__(message)
        self.addresses = addresses


@dataclass(frozen=True)
class UsbDeviceAddress:
    """Represents a USB device location on the bus.

    Attributes:
        bus: USB bus number assigned by the host controller.
        address: Device address on the bus (may change on re-enumeration).
        port_numbers: Tuple of physical port numbers from root hub to device.
                      Stable across re-enumeration as long as the device stays
                      in the same physical port.
    """

    bus: int
    address: int
    port_numbers: tuple[int, ...] | None = None

    @property
    def port_path(self) -> str | None:
        """Physical port path string (e.g. '1-2.3.1').

        Stable identifier that doesn't change when the device is
        re-plugged into the same physical USB port.
        Returns None if port information is unavailable.
        """
        if not self.port_numbers:
            return None
        return f"{self.bus}-" + ".".join(str(p) for p in self.port_numbers)

    def __str__(self) -> str:
        parts = [f"Bus {self.bus:03d} Device {self.address:03d}"]
        if path := self.port_path:
            parts.append(f"Port path: {path}")
        return ", ".join(parts)


def find_device_address(
    vid: int,
    pid: int,
    serial: str | None = None,
) -> UsbDeviceAddress:
    """Find a USB device by VID, PID, and optional serial number.

    Scans all connected USB devices and returns the bus address of the one
    matching the given criteria.

    Args:
        vid: USB Vendor ID (e.g. 0x1D50).
        pid: USB Product ID (e.g. 0x6018).
        serial: USB serial number string from the device descriptor.
                If None, exactly one device with matching VID/PID must exist.

    Returns:
        UsbDeviceAddress containing bus number, device address,
        and physical port path.

    Raises:
        DeviceNotFoundError: No device matches the given criteria.
        MultipleDevicesFoundError: Multiple devices match VID/PID
            and no serial was provided to disambiguate.
    """
    backend = _get_usb_backend()
    devices = list(
        usb.core.find(find_all=True, idVendor=vid, idProduct=pid, backend=backend)
    )

    if not devices:
        raise DeviceNotFoundError(
            f"No USB device found with VID=0x{vid:04X}, PID=0x{pid:04X}"
        )

    if serial is not None:
        return _find_by_serial(devices, vid, pid, serial)

    if len(devices) > 1:
        raise MultipleDevicesFoundError(
            f"Found {len(devices)} devices with VID=0x{vid:04X}, PID=0x{pid:04X}. "
            f"Provide a serial number to disambiguate.",
            addresses=[_extract_address(d) for d in devices],
        )

    return _extract_address(devices[0])


def _find_by_serial(
    devices: list[usb.core.Device],
    vid: int,
    pid: int,
    serial: str,
) -> UsbDeviceAddress:
    """Match a specific device by serial number from the list of candidates."""
    matches: list[usb.core.Device] = []
    for dev in devices:
        try:
            dev_serial = usb.util.get_string(dev, dev.iSerialNumber)
        except (usb.core.USBError, ValueError):
            continue
        if dev_serial == serial:
            matches.append(dev)

    if not matches:
        raise DeviceNotFoundError(
            f"No USB device found with VID=0x{vid:04X}, PID=0x{pid:04X}, "
            f"Serial='{serial}'"
        )

    if len(matches) > 1:
        raise MultipleDevicesFoundError(
            f"Found {len(matches)} devices with VID=0x{vid:04X}, "
            f"PID=0x{pid:04X}, Serial='{serial}'",
            addresses=[_extract_address(d) for d in matches],
        )

    return _extract_address(matches[0])


def _extract_address(dev: usb.core.Device) -> UsbDeviceAddress:
    """Extract bus address information from a pyusb device object."""
    port_numbers = dev.port_numbers
    return UsbDeviceAddress(
        bus=dev.bus,
        address=dev.address,
        port_numbers=tuple(port_numbers) if port_numbers else None,
    )


def _parse_id(value: str) -> int:
    """Parse a USB ID from hex (0x...) or decimal string."""
    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid ID '{value}'. Use hex (0x1234) or decimal (4660)."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find USB device bus address by VID, PID, and serial number.",
    )
    parser.add_argument(
        "--vid",
        type=_parse_id,
        required=True,
        help="USB Vendor ID (hex or decimal, e.g. 0x1D50)",
    )
    parser.add_argument(
        "--pid",
        type=_parse_id,
        required=True,
        help="USB Product ID (hex or decimal, e.g. 0x6018)",
    )
    parser.add_argument(
        "--serial",
        type=str,
        default=None,
        help="USB device serial number from descriptor",
    )

    args = parser.parse_args()

    try:
        addr = find_device_address(args.vid, args.pid, args.serial)
    except DeviceNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except MultipleDevicesFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        for device_addr in e.addresses:
            print(f"  - {device_addr}", file=sys.stderr)
        return 1

    print(f"Device found: {addr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

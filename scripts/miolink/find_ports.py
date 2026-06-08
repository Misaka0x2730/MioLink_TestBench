"""MioLink serial port discovery module.

Finds the GDB and UART serial ports of a MioLink device
by its USB bus address. MioLink exposes two CDC ACM interfaces:
  - Interface 0: GDB server
  - Interface 2: UART bridge

Usage as module:
    from usbutil.find_device import find_device_address
    from miolink import find_serial_ports

    addr = find_device_address(vid=0x1D50, pid=0x6018, serial="XXXX")
    ports = find_serial_ports(addr)
    print(ports.gdb, ports.uart)

Usage as CLI:
    python miolink/find_ports.py --path 1-1.3.4
    python miolink/find_ports.py --vid 0x1D50 --pid 0x6018 --serial XXXX
"""

from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow `from usbutil.find_device import ...` whether this file is
# run as a standalone CLI or imported as `miolink.find_ports` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from usbutil.find_device import UsbDeviceAddress, find_device_address

_PLATFORM = platform.system()

# Indices of the CDC IAD pairs exposed by the MioLink firmware:
# CDC pair 0 is the GDB server, CDC pair 1 is the target UART bridge.
#
# We index by CDC pair, not by raw bInterfaceNumber, because the tty/cu
# file is attached to different members of the CDC pair depending on the
# OS:
#   * Linux  — cdc-acm binds to the control interface (class 0x02);
#              the tty link sits under the EVEN interface (0, 2, ...).
#   * macOS  — IOSerialBSDClient attaches to the data interface
#              (class 0x0A); IOCalloutDevice sits under the ODD
#              interface (1, 3, ...).
# In a USB CDC IAD the control and data interfaces are always
# consecutive, so `iface_num // 2` gives the same pair index on both
# platforms.
_GDB_CDC_PAIR = 0
_UART_CDC_PAIR = 1


class PortNotFoundError(Exception):
    """Raised when serial ports cannot be found for a MioLink device."""


@dataclass(frozen=True)
class MioLinkPorts:
    """A pair of MioLink serial ports.

    Attributes:
        gdb: Path to the GDB server serial port.
        uart: Path to the UART bridge serial port.
    """

    gdb: str
    uart: str

    def __str__(self) -> str:
        return f"GDB: {self.gdb}, UART: {self.uart}"


def find_serial_ports(address: UsbDeviceAddress) -> MioLinkPorts:
    """Find GDB and UART serial ports for a MioLink at the given bus address.

    Args:
        address: USB device address (from find_device_address).

    Returns:
        MioLinkPorts with paths to both serial ports.

    Raises:
        PortNotFoundError: One or both ports could not be found.
    """
    if _PLATFORM == "Darwin":
        iface_map = _find_ports_darwin(address)
    elif _PLATFORM == "Linux":
        iface_map = _find_ports_linux(address)
    else:
        raise NotImplementedError(f"Unsupported platform: {_PLATFORM}")

    # Normalise raw bInterfaceNumber → CDC IAD pair index, so the same
    # lookup works whether the OS attached the tty to the control
    # interface (Linux) or to the data interface (macOS).
    pair_map: dict[int, str] = {
        iface_num // 2: path for iface_num, path in iface_map.items()
    }

    gdb = pair_map.get(_GDB_CDC_PAIR)
    uart = pair_map.get(_UART_CDC_PAIR)

    if gdb is None and uart is None:
        raise PortNotFoundError(
            f"No serial ports found for device at {address}"
        )
    if gdb is None:
        raise PortNotFoundError(
            f"GDB port (CDC pair {_GDB_CDC_PAIR}) not found "
            f"for device at {address}"
        )
    if uart is None:
        raise PortNotFoundError(
            f"UART port (CDC pair {_UART_CDC_PAIR}) not found "
            f"for device at {address}"
        )

    return MioLinkPorts(gdb=gdb, uart=uart)


# ── macOS ────────────────────────────────────────────────────────────


def _find_ports_darwin(address: UsbDeviceAddress) -> dict[int, str]:
    """Map interface numbers to /dev/cu.* paths on macOS via ioreg."""
    target = address.port_path
    if not target:
        raise PortNotFoundError("port_path is required on macOS")

    try:
        result = subprocess.run(
            ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l", "-w0"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise PortNotFoundError(f"ioreg failed: {e}")

    if result.returncode != 0:
        raise PortNotFoundError(f"ioreg returned {result.returncode}")

    current_path: str | None = None
    current_iface: int | None = None
    port_map: dict[int, str] = {}

    for line in result.stdout.splitlines():
        loc_match = re.search(r'"locationID"\s*=\s*(\d+)', line)
        if loc_match:
            loc_id = int(loc_match.group(1))
            new_path = _location_id_to_port_path(loc_id)
            if current_path == target and new_path != target:
                break
            current_path = new_path
            current_iface = None
            continue

        if current_path != target:
            continue

        iface_match = re.search(r'"bInterfaceNumber"\s*=\s*(\d+)', line)
        if iface_match:
            current_iface = int(iface_match.group(1))
            continue

        if current_iface is not None:
            callout_match = re.search(
                r'"IOCalloutDevice"\s*=\s*"([^"]+)"', line,
            )
            if callout_match:
                port_map[current_iface] = callout_match.group(1)

    return port_map


def _location_id_to_port_path(location_id: int) -> str:
    """Convert macOS IOKit locationID to USB port path.

    locationID encodes the bus in the top byte and port numbers
    in subsequent nibbles (4 bits each), zero-terminated.
    Example: 0x01134000 -> bus=1, ports=[1,3,4] -> "1-1.3.4"
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


# ── Linux ────────────────────────────────────────────────────────────


def _find_ports_linux(address: UsbDeviceAddress) -> dict[int, str]:
    """Map interface numbers to /dev/ttyACM* paths on Linux via sysfs."""
    port_path = address.port_path
    if not port_path:
        raise PortNotFoundError("port_path is required on Linux")

    sysfs_dev = Path(f"/sys/bus/usb/devices/{port_path}")
    if not sysfs_dev.exists():
        raise PortNotFoundError(f"USB device not in sysfs: {sysfs_dev}")

    port_map: dict[int, str] = {}

    for iface_dir in sorted(sysfs_dev.glob(f"{port_path}:*")):
        parts = iface_dir.name.rsplit(".", 1)
        if len(parts) != 2:
            continue

        try:
            iface_num = int(parts[1])
        except ValueError:
            continue

        tty_dir = iface_dir / "tty"
        if not tty_dir.exists():
            continue

        for tty_entry in sorted(tty_dir.iterdir()):
            dev_path = f"/dev/{tty_entry.name}"
            if Path(dev_path).exists():
                port_map[iface_num] = dev_path
                break

    return port_map


# ── CLI ──────────────────────────────────────────────────────────────


def _parse_port_path(value: str) -> str:
    if "-" not in value:
        raise argparse.ArgumentTypeError(
            f"Invalid port path '{value}'. "
            f"Expected BUS-PORT.PORT... (e.g. 1-1.3.4)"
        )
    return value


def _parse_id(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid ID '{value}'. Use hex (0x1234) or decimal (4660)."
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find GDB and UART serial ports of a MioLink device.",
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--path", type=_parse_port_path,
        help="USB port path (e.g. 1-1.3.4)",
    )
    target.add_argument(
        "--vid", type=_parse_id,
        help="USB Vendor ID (use with --pid)",
    )

    parser.add_argument(
        "--pid", type=_parse_id,
        help="USB Product ID (use with --vid)",
    )
    parser.add_argument(
        "--serial", type=str, default=None,
        help="USB device serial number",
    )

    args = parser.parse_args()

    try:
        if args.path:
            bus_str, ports_str = args.path.split("-", 1)
            address = UsbDeviceAddress(
                bus=int(bus_str), address=0,
                port_numbers=tuple(int(p) for p in ports_str.split(".")),
            )
        else:
            if args.pid is None:
                parser.error("--pid is required when using --vid")
            address = find_device_address(args.vid, args.pid, args.serial)

        ports = find_serial_ports(address)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(ports)
    return 0


if __name__ == "__main__":
    sys.exit(main())

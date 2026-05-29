"""BMP session module — shared GDB session management for Black Magic Probe.

Provides BmpTargetConfig for connection parameters and BmpSession as a
context manager that handles the entire GDB lifecycle:
  1. Start GDB with the ELF (symbol resolution)
  2. Connect to BMP
  3. Configure connect-under-reset
  4. Scan for targets
  5. Check Vtref (optional)
  6. Verify target name
  7. Attach to target

Usage:
    from bmp_session import BmpSession, BmpTargetConfig

    config = BmpTargetConfig(
        gdb_port="/dev/cu.usbmodem1134401",
        elf_path="firmware.elf",
        target_name="STM32F1",
        expected_vtref=3.3,
    )
    with BmpSession(config) as session:
        session.monitor("erase_mass")
        session.exec_continue()
        ...
"""

from __future__ import annotations

import argparse
import enum
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pygdbmi.gdbcontroller import GdbController


# ── Enums & data ─────────────────────────────────────────────────────


class ScanInterface(enum.Enum):
    SWD = "swd"
    JTAG = "jtag"


@dataclass(frozen=True)
class ScannedTarget:
    """A target discovered during BMP scan."""

    id: int
    name: str


@dataclass
class BmpTargetConfig:
    """Common configuration for BMP target connection.

    Attributes:
        gdb_port: Serial port of the BMP GDB server.
        elf_path: Path to the .elf file (loaded into GDB for symbols).
        target_name: Expected target name (substring match, case-insensitive).
        interface: Scan interface (SWD or JTAG).
        connect_under_reset: Hold nRST during scan/attach.
        gdb_executable: Path to arm-none-eabi-gdb.
        expected_vtref: If set, verify target voltage is within tolerance.
        vtref_tolerance: Acceptable Vtref deviation in Volts.
        frequency_hz: Debug-interface clock in Hz. *None* leaves the
            probe's default. Applied before the scan so scan and flash
            both run at this frequency.
    """

    gdb_port: str
    elf_path: Path | str
    target_name: str
    interface: ScanInterface = ScanInterface.SWD
    connect_under_reset: bool = False
    gdb_executable: str = "arm-none-eabi-gdb"
    expected_vtref: float | None = None
    vtref_tolerance: float = 0.3
    frequency_hz: int | None = None


# ── Exceptions ───────────────────────────────────────────────────────


class BmpError(Exception):
    """Base exception for all BMP operations."""


class BmpConnectionError(BmpError):
    """Failed to connect GDB to the Black Magic Probe."""


class ScanError(BmpError):
    """Target scan failed or returned no results."""


class TargetMismatchError(BmpError):
    """Scanned targets do not match the expected target name."""


class VtrefError(BmpError):
    """Target reference voltage check failed."""


class FrequencyError(BmpError):
    """Failed to set the debug interface frequency."""


# ── BmpSession ───────────────────────────────────────────────────────


class BmpSession:
    """Manages a GDB session with a Black Magic Probe.

    Use as a context manager::

        with BmpSession(config) as session:
            session.monitor("erase_mass")
            session.exec_continue()
            ...

    On entry the session performs the full setup pipeline:
    connect → configure connect_rst → scan → check vtref →
    verify target → attach.

    On exit it kills (detaches + resets) the target and exits GDB.
    """

    def __init__(self, config: BmpTargetConfig) -> None:
        self._config = config
        self._gdb: GdbController | None = None
        self._targets: list[ScannedTarget] = []
        self._vtref: float | None = None
        self._scan_lines: list[str] = []

    def __enter__(self) -> BmpSession:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── Properties ───────────────────────────────────────────────

    @property
    def config(self) -> BmpTargetConfig:
        return self._config

    @property
    def gdb(self) -> GdbController:
        """Raw GdbController for advanced / one-off GDB commands."""
        if self._gdb is None:
            raise RuntimeError("BmpSession is not open")
        return self._gdb

    @property
    def targets(self) -> list[ScannedTarget]:
        """Targets discovered during scan."""
        return list(self._targets)

    @property
    def vtref(self) -> float | None:
        """Measured target reference voltage, or *None* if not reported."""
        return self._vtref

    # ── Lifecycle ────────────────────────────────────────────────

    def open(self) -> None:
        """Open the session: start GDB, connect, scan, verify, attach."""
        elf_path = Path(self._config.elf_path)
        if not elf_path.exists():
            raise FileNotFoundError(f"ELF file not found: {elf_path}")

        if not shutil.which(self._config.gdb_executable):
            raise FileNotFoundError(
                f"GDB executable not found: {self._config.gdb_executable}"
            )

        self._gdb = GdbController(
            command=[
                self._config.gdb_executable,
                "--nx", "--quiet", "--interpreter=mi3",
                str(elf_path),
            ],
        )

        try:
            self._do_connect()
            self._do_setup_connect_rst()
            self._do_set_frequency()
            self._do_scan()
            self._do_check_vtref()
            self._do_verify_target()
            self._do_attach()
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        """Close the session: kill target, exit GDB cleanly, release the port."""
        if self._gdb is None:
            return
        gdb, self._gdb = self._gdb, None

        try:
            gdb.write("kill", timeout_sec=5, raise_error_on_timeout=False)
        except Exception:
            pass

        # Ask GDB to disconnect from BMP and exit cleanly, so the serial
        # port is released before we fall back to SIGTERM.
        try:
            gdb.write(
                "-gdb-exit", timeout_sec=2, raise_error_on_timeout=False,
            )
        except Exception:
            pass

        # SIGTERM with a SIGKILL fallback so close() can never hang.
        proc = gdb.gdb_process
        if proc is not None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                pass
        try:
            gdb.exit()
        except Exception:
            pass

    # ── Public operations ────────────────────────────────────────

    def monitor(self, command: str, timeout_sec: float = 10) -> list[str]:
        """Send a BMP monitor command, return console output lines.

        `pygdbmi.write()` can return before the ^done result record
        arrives when the remote emits output in several packets with
        gaps between them (e.g. `monitor swdp_scan` on a MioLink
        rev. B, where the VTref ADC read introduces a delay). We keep
        draining responses until a result record shows up or the
        overall timeout elapses, so the caller sees the full monitor
        output instead of a truncated prefix.
        """
        responses = self.gdb.write(
            f"monitor {command}", timeout_sec=timeout_sec,
            raise_error_on_timeout=False,
        )
        deadline = time.monotonic() + timeout_sec
        while not any(r.get("type") == "result" for r in responses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            extra = self.gdb.get_gdb_response(
                timeout_sec=min(2.0, remaining),
                raise_error_on_timeout=False,
            )
            if extra:
                responses.extend(extra)
        check_responses(responses, f"monitor {command}")
        return console_lines(responses)

    def exec_continue(self) -> None:
        """Resume target execution."""
        self.gdb.write("-exec-continue", timeout_sec=5)

    def exec_interrupt(self) -> None:
        """Halt target execution."""
        self.gdb.write("-exec-interrupt", timeout_sec=5)
        time.sleep(0.2)
        self.gdb.get_gdb_response(
            timeout_sec=3, raise_error_on_timeout=False,
        )

    def read_memory(self, address: int, size: int) -> bytes:
        """Read *size* bytes of target memory at *address*."""
        responses = self.gdb.write(
            f"-data-read-memory-bytes 0x{address:x} {size}",
            timeout_sec=10,
        )
        check_responses(responses, "read memory")

        for resp in responses:
            if resp.get("type") != "result":
                continue
            payload = resp.get("payload")
            if not isinstance(payload, dict):
                continue
            for block in payload.get("memory", []):
                contents = block.get("contents", "")
                if contents:
                    return bytes.fromhex(contents)

        raise BmpError(
            f"No data returned when reading {size} bytes at 0x{address:x}"
        )

    def write_memory(self, address: int, data: bytes) -> None:
        """Write *data* to target memory at *address*."""
        responses = self.gdb.write(
            f"-data-write-memory-bytes 0x{address:x} {data.hex()}",
            timeout_sec=10,
        )
        check_responses(responses, "write memory")

    def get_buffer_info(self, name: str) -> tuple[int, int]:
        """Resolve a C symbol to *(address, size)* via GDB expressions.

        Raises:
            BmpError: Symbol cannot be found in the loaded ELF.
        """
        size_resp = self.gdb.write(
            f'-data-evaluate-expression "sizeof({name})"',
            timeout_sec=5,
        )
        check_responses(size_resp, f"get sizeof({name})")
        size_str = _extract_value(size_resp)
        if not size_str:
            raise BmpError(
                f"Cannot resolve sizeof({name}) — symbol not found in ELF"
            )
        size = int(size_str)

        addr_resp = self.gdb.write(
            f'-data-evaluate-expression "(unsigned long)(&{name})"',
            timeout_sec=5,
        )
        check_responses(addr_resp, f"get address of {name}")
        addr_str = _extract_value(addr_resp)
        if not addr_str:
            raise BmpError(
                f"Cannot resolve &{name} — symbol not found in ELF"
            )
        address = int(addr_str)

        return address, size

    # ── Private setup steps ──────────────────────────────────────

    def _do_connect(self) -> None:
        # Enable MI async mode so that `-exec-continue` returns control to
        # us immediately (running in background) and `-exec-interrupt` can
        # later halt the target. Without this the GDB MI loop blocks
        # inside `-exec-continue` and `-exec-interrupt` times out.
        self.gdb.write("-gdb-set mi-async on", timeout_sec=5)

        responses = self.gdb.write(
            f"target extended-remote {self._config.gdb_port}",
            timeout_sec=10,
        )
        for resp in responses:
            if resp.get("type") == "result" and resp.get("message") == "error":
                raise BmpConnectionError(
                    f"Cannot connect to BMP at {self._config.gdb_port}: "
                    f"{_extract_error_msg(resp)}"
                )

    def _do_setup_connect_rst(self) -> None:
        if self._config.connect_under_reset:
            self.monitor("connect_rst enable")
        else:
            self.monitor("connect_rst disable")

    def _do_set_frequency(self) -> None:
        freq = self._config.frequency_hz
        if freq is None:
            return

        lines = self.monitor(f"frequency {freq}")

        for line in lines:
            low = line.lower()
            if "fixed" in low:
                raise FrequencyError(
                    f"Probe reports a fixed debug interface frequency; "
                    f"cannot set {freq} Hz"
                )
            if "must be" in low or "invalid" in low:
                raise FrequencyError(
                    f"Probe rejected frequency {freq} Hz: {line.strip()}"
                )

    def _do_scan(self) -> None:
        iface = self._config.interface
        scan_cmd = (
            "swdp_scan" if iface is ScanInterface.SWD else "jtag_scan"
        )
        self._scan_lines = self.monitor(scan_cmd)
        self._targets = _parse_scan_output(self._scan_lines)

        if not self._targets:
            raise ScanError(
                f"No targets found via {iface.value.upper()}. "
                f"Scan output:\n" + "\n".join(self._scan_lines)
            )

    def _do_check_vtref(self) -> None:
        self._vtref = _parse_vtref(self._scan_lines)

        if self._config.expected_vtref is None:
            return

        if self._vtref is None:
            return

        if self._vtref == 0.0:
            raise VtrefError(
                f"Target voltage is absent, "
                f"expected {self._config.expected_vtref}V"
            )

        diff = abs(self._vtref - self._config.expected_vtref)
        if diff > self._config.vtref_tolerance:
            raise VtrefError(
                f"Target voltage {self._vtref:.2f}V is outside expected range "
                f"{self._config.expected_vtref}V "
                f"± {self._config.vtref_tolerance}V"
            )

    def _do_verify_target(self) -> None:
        expected = self._config.target_name
        for target in self._targets:
            if expected.lower() in target.name.lower():
                return

        found = ", ".join(f"'{t.name}'" for t in self._targets)
        raise TargetMismatchError(
            f"Expected target containing '{expected}', but found: {found}"
        )

    def _do_attach(self) -> None:
        responses = self.gdb.write(
            f"attach {self._targets[0].id}", timeout_sec=10,
        )
        check_responses(
            responses, f"attach to target {self._targets[0].id}",
        )


# ── Public response-parsing helpers ──────────────────────────────────


def check_responses(
    responses: list[dict],
    context: str,
    error_cls: type[BmpError] = BmpError,
) -> None:
    """Raise *error_cls* if any response indicates a GDB error."""
    for resp in responses:
        if resp.get("type") == "result" and resp.get("message") == "error":
            raise error_cls(
                f"Failed to {context}: {_extract_error_msg(resp)}"
            )


def console_lines(responses: list[dict]) -> list[str]:
    """Extract text output lines from a GDB/MI response list.

    Includes both ``~"..."`` console-stream records (output from GDB
    itself) and ``@"..."`` target-stream records (output from the
    remote, e.g. BMP ``monitor`` replies). Payloads are concatenated
    before splitting on newlines: a single logical line may arrive
    across multiple stream records.
    """
    joined = "".join(
        resp.get("payload", "") for resp in responses
        if resp.get("type") in ("console", "target")
    )
    return joined.splitlines()


# ── Private helpers ──────────────────────────────────────────────────


def _parse_scan_output(lines: list[str]) -> list[ScannedTarget]:
    """Parse BMP scan console output into a list of targets.

    Expected line format:  ``" 1      STM32F1 medium density M3"``
    """
    targets: list[ScannedTarget] = []
    for line in lines:
        match = re.match(r"\s*(\d+)\s+(.+)", line)
        if not match:
            continue
        target_id = int(match.group(1))
        raw_name = match.group(2).strip()
        if raw_name.lower().startswith(("att ", "driver")):
            continue
        targets.append(ScannedTarget(id=target_id, name=raw_name))
    return targets


def _parse_vtref(scan_lines: list[str]) -> float | None:
    """Extract target voltage from scan console output.

    Returns voltage in Volts, 0.0 for ``ABSENT``, or *None* if the
    probe does not report Vtref.
    """
    for line in scan_lines:
        m = re.search(
            r"Target voltage:\s*([0-9.]+)\s*V", line, re.IGNORECASE,
        )
        if m:
            return float(m.group(1))
        if re.search(r"Target voltage:\s*ABSENT", line, re.IGNORECASE):
            return 0.0
    return None


def _extract_value(responses: list[dict]) -> str:
    for resp in responses:
        if resp.get("type") != "result":
            continue
        payload = resp.get("payload")
        if isinstance(payload, dict):
            return payload.get("value", "")
    return ""


def _extract_error_msg(resp: dict) -> str:
    payload = resp.get("payload", {})
    if isinstance(payload, dict):
        return payload.get("msg", str(payload))
    return str(payload)


# ── CLI argument helpers ─────────────────────────────────────────────


def parse_frequency(value: str) -> int:
    """Parse a frequency CLI argument into Hz.

    Accepts a non-negative integer with an optional ``k``/``K`` (×1000)
    or ``M`` (×1_000_000) suffix. Examples: ``4000000``, ``4000k``,
    ``4M``. Raises :class:`argparse.ArgumentTypeError` on bad input.
    """
    m = re.fullmatch(r"\s*(\d+)\s*([kKM]?)\s*", value)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid frequency '{value}' "
            f"(expected integer Hz, optionally suffixed with k or M)"
        )
    hz = int(m.group(1))
    suffix = m.group(2)
    if suffix in ("k", "K"):
        hz *= 1000
    elif suffix == "M":
        hz *= 1_000_000
    if hz == 0:
        raise argparse.ArgumentTypeError("frequency must be greater than 0")
    return hz


def add_target_arguments(parser: argparse.ArgumentParser) -> None:
    """Add common BMP target arguments to an *argparse* parser.

    Adds: ``--port``, ``--interface``, ``--target``, ``--connect-rst``,
    ``--freq``, ``--gdb``, ``--vtref``, ``--vtref-tolerance``, and
    positional ``elf_file``.
    """
    parser.add_argument(
        "--port", required=True,
        help="BMP GDB serial port (e.g. /dev/cu.usbmodem..., /dev/ttyACM0)",
    )
    parser.add_argument(
        "--interface", choices=["swd", "jtag"], default="swd",
        help="Scan interface (default: swd)",
    )
    parser.add_argument(
        "--target", required=True,
        help="Expected target name (substring match, e.g. 'STM32F1')",
    )
    parser.add_argument(
        "--connect-rst", action="store_true",
        help="Enable connect under reset",
    )
    parser.add_argument(
        "--freq", type=parse_frequency, default=None, metavar="HZ",
        help="Debug interface frequency in Hz. Accepts 'k' or 'M' suffix "
             "(e.g. 4000000, 4000k, 4M). Skip to keep the probe default",
    )
    parser.add_argument(
        "--gdb", default="arm-none-eabi-gdb",
        help="Path to GDB executable (default: arm-none-eabi-gdb)",
    )
    parser.add_argument(
        "--vtref", type=float, default=None,
        help="Expected target voltage in Volts (e.g. 3.3). "
             "Skip check if omitted",
    )
    parser.add_argument(
        "--vtref-tolerance", type=float, default=0.3,
        help="Acceptable Vtref deviation in Volts (default: 0.3)",
    )
    parser.add_argument(
        "elf_file", type=Path,
        help="Path to the .elf firmware file",
    )


def config_from_args(args: argparse.Namespace) -> BmpTargetConfig:
    """Create a :class:`BmpTargetConfig` from parsed CLI arguments."""
    return BmpTargetConfig(
        gdb_port=args.port,
        elf_path=args.elf_file,
        target_name=args.target,
        interface=ScanInterface(args.interface),
        connect_under_reset=args.connect_rst,
        gdb_executable=args.gdb,
        expected_vtref=args.vtref,
        vtref_tolerance=args.vtref_tolerance,
        frequency_hz=args.freq,
    )

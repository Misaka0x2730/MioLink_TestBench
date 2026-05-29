"""BMP buffer test module.

Connects to a Black Magic Probe via GDB/MI, runs the target firmware,
and performs buffer value tests. Two test types are supported:

  write_and_verify:
    Run target -> halt -> zero the buffer -> resume ->
    halt -> read buffer -> compare with expected value.
    Proves the firmware actively writes the expected data to the buffer.

  verify_only:
    Run target -> halt -> read buffer -> compare with expected value.
    Proves the buffer already holds the expected data.

Usage as module:
    from gdb_bmp import BmpTargetConfig
    from tests.buffer.bmp_buffer_test import (
        run_buffer_test, BufferTestConfig, TestType,
    )

    target = BmpTargetConfig(
        gdb_port="/dev/cu.usbmodem1134401",
        elf_path="firmware.elf",
        target_name="STM32F1",
    )
    test = BufferTestConfig(
        buffer_name="test_buffer",
        expected_value=bytes.fromhex("DEADBEEF"),
        test_type=TestType.WRITE_AND_VERIFY,
    )
    run_buffer_test(target, test)

Usage as CLI:
    python tests/buffer/bmp_buffer_test.py --port /dev/cu.usbmodem1134401 \\
        --target STM32F1 --buffer test_buffer \\
        --expected DEADBEEF --test write_and_verify firmware.elf
"""

from __future__ import annotations

import argparse
import enum
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Allow `from gdb_bmp.session import ...` whether this file is run as a
# standalone CLI or imported as `tests.buffer.bmp_buffer_test` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from gdb_bmp.session import (
    BmpError,
    BmpSession,
    BmpTargetConfig,
    add_target_arguments,
    config_from_args,
)


class TestType(enum.Enum):
    WRITE_AND_VERIFY = "write_and_verify"
    VERIFY_ONLY = "verify_only"


@dataclass
class BufferTestConfig:
    """Configuration for a BMP buffer test.

    Attributes:
        buffer_name: C symbol name of the buffer to test.
        expected_value: Expected buffer contents as raw bytes.
        test_type: WRITE_AND_VERIFY or VERIFY_ONLY.
        run_time_sec: Seconds to let the target run before each check.
    """

    buffer_name: str
    expected_value: bytes
    test_type: TestType
    run_time_sec: float = 3.0


# ── Exceptions ───────────────────────────────────────────────────────


class BufferTestError(BmpError):
    """Base exception for buffer test operations."""


class BufferNotFoundError(BufferTestError):
    """The buffer symbol was not found in the ELF."""


class BufferSizeMismatchError(BufferTestError):
    """Expected value size does not match the actual buffer size."""


class BufferMismatchError(BufferTestError):
    """Buffer contents do not match the expected value."""

    def __init__(
        self, message: str, expected: bytes, actual: bytes,
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual


# ── Public API ───────────────────────────────────────────────────────


def run_buffer_test(
    target_config: BmpTargetConfig,
    test_config: BufferTestConfig,
) -> None:
    """Run a buffer test on a target via Black Magic Probe.

    The ELF file is loaded into GDB for symbol resolution only —
    it is NOT flashed onto the target.

    Args:
        target_config: BMP connection and target configuration.
        test_config: Buffer test parameters.

    Raises:
        FileNotFoundError: ELF file or GDB executable not found.
        BmpError: GDB / BMP communication error.
        BufferNotFoundError: Buffer symbol not in ELF.
        BufferSizeMismatchError: Expected value size != buffer size.
        BufferMismatchError: Buffer contents don't match expected value.
    """
    with BmpSession(target_config) as session:
        try:
            buf_addr, buf_size = session.get_buffer_info(
                test_config.buffer_name,
            )
        except BmpError as exc:
            raise BufferNotFoundError(str(exc)) from exc

        if len(test_config.expected_value) != buf_size:
            raise BufferSizeMismatchError(
                f"Expected value is {len(test_config.expected_value)} bytes "
                f"but buffer '{test_config.buffer_name}' is {buf_size} bytes"
            )

        if test_config.test_type is TestType.WRITE_AND_VERIFY:
            _test_write_and_verify(session, test_config, buf_addr, buf_size)
        else:
            _test_verify_only(session, test_config, buf_addr, buf_size)


# ── Test implementations ─────────────────────────────────────────────


def _test_write_and_verify(
    session: BmpSession,
    config: BufferTestConfig,
    buf_addr: int,
    buf_size: int,
) -> None:
    session.exec_continue()
    time.sleep(config.run_time_sec)
    session.exec_interrupt()

    session.write_memory(buf_addr, b"\x00" * buf_size)

    session.exec_continue()
    time.sleep(config.run_time_sec)
    session.exec_interrupt()

    actual = session.read_memory(buf_addr, buf_size)
    _assert_buffer_match(config.buffer_name, config.expected_value, actual)


def _test_verify_only(
    session: BmpSession,
    config: BufferTestConfig,
    buf_addr: int,
    buf_size: int,
) -> None:
    session.exec_continue()
    time.sleep(config.run_time_sec)
    session.exec_interrupt()

    actual = session.read_memory(buf_addr, buf_size)
    _assert_buffer_match(config.buffer_name, config.expected_value, actual)


def _assert_buffer_match(
    name: str, expected: bytes, actual: bytes,
) -> None:
    if actual == expected:
        return

    raise BufferMismatchError(
        f"Buffer '{name}' mismatch:\n"
        f"  expected: {expected.hex()}\n"
        f"  actual:   {actual.hex()}",
        expected=expected,
        actual=actual,
    )


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a buffer test on a target via Black Magic Probe.",
    )
    add_target_arguments(parser)

    parser.add_argument(
        "--buffer", required=True,
        help="C symbol name of the buffer to test",
    )
    parser.add_argument(
        "--expected", required=True,
        help="Expected buffer value as hex string (e.g. DEADBEEF)",
    )
    parser.add_argument(
        "--test", choices=["write_and_verify", "verify_only"],
        default="write_and_verify",
        help="Test type (default: write_and_verify)",
    )
    parser.add_argument(
        "--run-time", type=float, default=3.0,
        help="Seconds to let the target run before each check (default: 3)",
    )

    args = parser.parse_args()

    try:
        expected_bytes = bytes.fromhex(args.expected)
    except ValueError:
        print(
            f"Error: Invalid hex string for --expected: '{args.expected}'",
            file=sys.stderr,
        )
        return 1

    target_config = config_from_args(args)
    test_config = BufferTestConfig(
        buffer_name=args.buffer,
        expected_value=expected_bytes,
        test_type=TestType(args.test),
        run_time_sec=args.run_time,
    )

    try:
        run_buffer_test(target_config, test_config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except BufferMismatchError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except BmpError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(
        f"PASS: buffer '{args.buffer}' matches expected value "
        f"({len(expected_bytes)} bytes)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

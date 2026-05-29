"""USB-UART echo test for the MioLink target serial bridge.

Drives the target-UART CDC interface (interface 2) of a MioLink probe
and verifies a digit-echo target firmware that returns each input byte
incremented by 1: '0'..'8' -> '1'..'9', '9' -> '0'.

Test variants (--test):
  random_large  Random payload size in [32, 1024] bytes per iteration.
                The main stress test for the UART bridge.
  endpoint_fs   Random payload size in [61, 67] bytes per iteration —
                exercises behaviour around the USB FS bulk endpoint
                MaxPacketSize of 64 (boundary / short-packet logic).
  fixed         Constant payload size --size per iteration.
  single_byte   One byte per iteration (sanity check).
  all           Runs random_large then endpoint_fs back-to-back.

Usage:
    python tests/uart/uart_echo_test.py --port /dev/cu.usbmodemXXX \\
        --baudrate 115200 --iterations 100 --test random_large

    python tests/uart/uart_echo_test.py --port /dev/ttyACM1 \\
        --test all --iterations 50 --seed 42
"""

from __future__ import annotations

import argparse
import enum
import random
import sys
import time
from dataclasses import dataclass

try:
    import serial
except ImportError:
    print(
        "Error: pyserial is required. Install with: "
        "pip install pyserial",
        file=sys.stderr,
    )
    sys.exit(2)


_DIGIT_MIN = ord("0")
_DIGIT_MAX = ord("9")

_RANDOM_LARGE_MIN = 32
_RANDOM_LARGE_MAX = 1024

_ENDPOINT_FS_MIN = 61
_ENDPOINT_FS_MAX = 67


# ── Configuration ────────────────────────────────────────────────────


class TestType(enum.Enum):
    RANDOM_LARGE = "random_large"
    ENDPOINT_FS = "endpoint_fs"
    FIXED = "fixed"
    SINGLE_BYTE = "single_byte"
    ALL = "all"


@dataclass
class UartTestConfig:
    """Configuration for a UART echo test run.

    Attributes:
        port: Serial port path (e.g. /dev/cu.usbmodem..., /dev/ttyACM1).
        baudrate: Baud rate in bits per second.
        bytesize: Data bits (5, 6, 7, or 8).
        parity: Parity character: 'N', 'E', 'O', 'M', or 'S'.
        stopbits: Stop bits (1, 1.5, or 2).
        read_timeout: Inactivity timeout for each read chunk, in seconds.
        test_type: Which test variant to run.
        iterations: Number of iterations per test variant.
        size: Fixed payload size (used only by TestType.FIXED).
        seed: Optional RNG seed for reproducible payloads.
        warmup_sec: Delay after opening the port before sending data,
            to let the bridge / target settle.
    """

    port: str
    baudrate: int = 115200
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0
    read_timeout: float = 2.0
    test_type: TestType = TestType.RANDOM_LARGE
    iterations: int = 100
    size: int = 64
    seed: int | None = None
    warmup_sec: float = 0.2


# ── Exceptions ───────────────────────────────────────────────────────


class UartTestError(Exception):
    """Base exception for UART echo test failures."""


class EchoTimeoutError(UartTestError):
    """The target did not return the expected number of bytes in time."""


class EchoMismatchError(UartTestError):
    """A byte read back did not match the expected (sent + 1)."""


# ── Core test logic ──────────────────────────────────────────────────


def expected_echo(payload: bytes) -> bytes:
    """Compute the expected echo for a payload of '0'..'9' bytes.

    Each byte is incremented by 1; '9' wraps back to '0'. Bytes outside
    the '0'..'9' range are not expected in practice; this helper does
    not validate them and will produce out-of-range output for them.
    """
    return bytes(
        _DIGIT_MIN if b == _DIGIT_MAX else b + 1 for b in payload
    )


def make_payload(size: int, rng: random.Random) -> bytes:
    """Generate a random byte string of digits '0'..'9' of given length."""
    return bytes(rng.randint(_DIGIT_MIN, _DIGIT_MAX) for _ in range(size))


def _iter_sizes(
    test_type: TestType,
    iterations: int,
    fixed_size: int,
    rng: random.Random,
) -> list[int]:
    """Return per-iteration payload sizes for a single test variant.

    *test_type* must not be ``TestType.ALL`` — composite variants are
    expanded by :func:`run_uart_test` before reaching this helper.
    """
    if test_type is TestType.RANDOM_LARGE:
        return [
            rng.randint(_RANDOM_LARGE_MIN, _RANDOM_LARGE_MAX)
            for _ in range(iterations)
        ]
    if test_type is TestType.ENDPOINT_FS:
        return [
            rng.randint(_ENDPOINT_FS_MIN, _ENDPOINT_FS_MAX)
            for _ in range(iterations)
        ]
    if test_type is TestType.FIXED:
        return [fixed_size] * iterations
    if test_type is TestType.SINGLE_BYTE:
        return [1] * iterations
    raise ValueError(f"Unsupported test type for sizing: {test_type}")


def _read_exact(
    ser: serial.Serial, size: int, timeout: float,
) -> bytes:
    """Read exactly *size* bytes from *ser* with an inactivity *timeout*.

    The timeout is reset every time at least one byte arrives, so a
    slow but steady stream will still complete. Raises
    :class:`EchoTimeoutError` if the deadline expires before *size*
    bytes have been received.
    """
    received = bytearray()
    deadline = time.monotonic() + timeout
    while len(received) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EchoTimeoutError(
                f"Timeout: expected {size} bytes, "
                f"received {len(received)} ({bytes(received)!r})"
            )
        ser.timeout = remaining
        chunk = ser.read(size - len(received))
        if chunk:
            received += chunk
            deadline = time.monotonic() + timeout
    return bytes(received)


def _format_mismatch(
    sent: bytes, expected: bytes, received: bytes, max_diffs: int = 8,
) -> str:
    """Return a compact human-readable diff between expected and received.

    Tolerates len(sent) != len(expected) != len(received): positions past
    the end of any one buffer are rendered as ``--`` rather than indexing
    out of range, and a length-only mismatch is only labelled as such
    when no per-byte differences were found within the common prefix.
    """
    def show(buf: bytes, idx: int) -> tuple[str, str]:
        if idx >= len(buf):
            return "--", "--"
        return repr(chr(buf[idx])), f"0x{buf[idx]:02x}"

    diffs: list[str] = []
    truncated = False
    common = min(len(expected), len(received))
    n = max(len(expected), len(received))
    for idx in range(n):
        s_in_range = idx < len(expected)
        r_in_range = idx < len(received)
        if s_in_range and r_in_range and expected[idx] == received[idx]:
            continue
        sent_chr, sent_hex = show(sent, idx)
        exp_chr, exp_hex = show(expected, idx)
        got_chr, got_hex = show(received, idx)
        diffs.append(
            f"  [{idx:4d}] sent={sent_chr} expected={exp_chr} got={got_chr} "
            f"({sent_hex} -> {exp_hex} / {got_hex})"
        )
        if len(diffs) >= max_diffs:
            truncated = idx + 1 < n
            break

    if not diffs:
        diffs.append(
            "  (length differs only; common prefix matches)"
            if len(expected) != len(received)
            else "  (no per-byte diffs found — buffers compare equal?)"
        )
    elif truncated:
        diffs.append("  ...")

    return (
        f"Echo mismatch: sent {len(sent)} bytes, "
        f"expected {len(expected)} bytes, received {len(received)} bytes "
        f"(common prefix {common} bytes)\n" + "\n".join(diffs)
    )


def _run_variant(
    ser: serial.Serial,
    test_type: TestType,
    cfg: UartTestConfig,
    rng: random.Random,
) -> tuple[int, float]:
    """Run a single non-composite test variant.

    Returns a (total_bytes_sent, elapsed_seconds) pair.
    """
    sizes = _iter_sizes(test_type, cfg.iterations, cfg.size, rng)
    print(
        f"\n=== {test_type.value}: {cfg.iterations} iterations "
        f"(size range: {min(sizes)}..{max(sizes)} bytes) ==="
    )

    total_bytes = 0
    start = time.monotonic()
    for i, size in enumerate(sizes, 1):
        payload = make_payload(size, rng)
        expected = expected_echo(payload)

        ser.write(payload)
        ser.flush()

        try:
            received = _read_exact(ser, size, cfg.read_timeout)
        except EchoTimeoutError as e:
            raise EchoTimeoutError(f"[iter {i}/{cfg.iterations}] {e}") from e

        if received != expected:
            raise EchoMismatchError(
                f"[iter {i}/{cfg.iterations}] "
                + _format_mismatch(payload, expected, received)
            )

        total_bytes += size
        print(f"[iter {i:4d}/{cfg.iterations}] {size:5d} bytes OK")

    elapsed = time.monotonic() - start
    return total_bytes, elapsed


def run_uart_test(cfg: UartTestConfig) -> None:
    """Open the serial port and run the configured test variant(s).

    Raises:
        UartTestError: Any echo mismatch or timeout aborts the run.
        serial.SerialException: Underlying serial port failure.
    """
    rng = random.Random(cfg.seed)

    if cfg.test_type is TestType.ALL:
        variants = [TestType.RANDOM_LARGE, TestType.ENDPOINT_FS]
    else:
        variants = [cfg.test_type]

    seed_note = f", seed={cfg.seed}" if cfg.seed is not None else ""
    print(
        f"Opening {cfg.port} @ {cfg.baudrate} {cfg.bytesize}{cfg.parity}"
        f"{cfg.stopbits}{seed_note}"
    )

    with serial.Serial(
        port=cfg.port,
        baudrate=cfg.baudrate,
        bytesize=cfg.bytesize,
        parity=cfg.parity,
        stopbits=cfg.stopbits,
        timeout=cfg.read_timeout,
        write_timeout=cfg.read_timeout,
    ) as ser:
        if cfg.warmup_sec > 0:
            time.sleep(cfg.warmup_sec)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        grand_total = 0
        grand_elapsed = 0.0
        for variant in variants:
            total, elapsed = _run_variant(ser, variant, cfg, rng)
            rate = total / elapsed if elapsed > 0 else 0.0
            print(
                f"  -> {variant.value}: {total} bytes in {elapsed:.2f}s "
                f"({rate:,.0f} B/s)"
            )
            grand_total += total
            grand_elapsed += elapsed

        if len(variants) > 1:
            rate = grand_total / grand_elapsed if grand_elapsed > 0 else 0.0
            print(
                f"\nTOTAL: {grand_total} bytes in {grand_elapsed:.2f}s "
                f"({rate:,.0f} B/s)"
            )
        print("\nPASSED")


# ── CLI ──────────────────────────────────────────────────────────────


def _positive_int(value: str) -> int:
    try:
        n = int(value, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}")
    if n <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive integer, got {n}"
        )
    return n


def _positive_float(value: str) -> float:
    try:
        x = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}")
    if x <= 0:
        raise argparse.ArgumentTypeError(
            f"must be a positive number, got {x}"
        )
    return x


def _non_negative_float(value: str) -> float:
    try:
        x = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid float: {value!r}")
    if x < 0:
        raise argparse.ArgumentTypeError(
            f"must be >= 0, got {x}"
        )
    return x


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "USB-UART echo test for the MioLink target serial bridge. "
            "The target firmware must echo each byte incremented by 1 "
            "('0'..'8' -> '1'..'9', '9' -> '0')."
        ),
    )

    parser.add_argument(
        "--port", required=True,
        help=(
            "Serial port path of the MioLink target UART CDC "
            "(e.g. /dev/cu.usbmodem..., /dev/ttyACM1)"
        ),
    )
    parser.add_argument(
        "--baudrate", type=_positive_int, default=115200,
        help="Baud rate (default: 115200)",
    )
    parser.add_argument(
        "--bytesize", type=int, choices=[5, 6, 7, 8], default=8,
        help="Data bits (default: 8)",
    )
    parser.add_argument(
        "--parity", choices=["N", "E", "O", "M", "S"], default="N",
        help="Parity: N=none, E=even, O=odd, M=mark, S=space (default: N)",
    )
    parser.add_argument(
        "--stopbits", type=float, choices=[1, 1.5, 2], default=1,
        help="Stop bits (default: 1)",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=2.0, dest="read_timeout",
        help="Per-chunk read inactivity timeout in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--test", choices=[t.value for t in TestType],
        default=TestType.RANDOM_LARGE.value,
        help="Test variant to run (default: random_large)",
    )
    parser.add_argument(
        "--iterations", type=_positive_int, default=100,
        help="Number of iterations per variant (default: 100)",
    )
    parser.add_argument(
        "--size", type=_positive_int, default=64,
        help="Payload size in bytes for --test fixed (default: 64)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="RNG seed for reproducible payloads (default: random)",
    )
    parser.add_argument(
        "--warmup", type=_non_negative_float, default=0.2, dest="warmup_sec",
        help="Delay in seconds after opening the port (default: 0.2)",
    )

    args = parser.parse_args()

    cfg = UartTestConfig(
        port=args.port,
        baudrate=args.baudrate,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        read_timeout=args.read_timeout,
        test_type=TestType(args.test),
        iterations=args.iterations,
        size=args.size,
        seed=args.seed,
        warmup_sec=args.warmup_sec,
    )

    try:
        run_uart_test(cfg)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except UartTestError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except serial.SerialException as e:
        print(f"Serial error: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())

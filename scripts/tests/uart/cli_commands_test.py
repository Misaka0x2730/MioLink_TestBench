"""CLI command tests for the MioLink test target.

Drives the test firmware's embedded CLI over a UART serial port
(``CLI_MODE_UART`` build) and validates every command the target
exposes in :file:`firmware/application/main.c`:

  id        Print unique platform ID                 (registered binding)
  platform  Print general platform info              (registered binding)
  help      Print list of commands (and per-command  (built-in binding)
            help via ``help <name>``)

Plus negative paths:

  - unknown command  ->  "Unknown command: ..."
  - empty input      ->  only a fresh prompt is reprinted

Embedded CLI wire protocol (see ``firmware/external/embedded-cli``):

  * Every printable byte written by the host is echoed back.
  * On ``\\r`` (or ``\\n``) the CLI writes ``\\r\\n``, executes the
    command, then writes the prompt (default ``"> "``).
  * Each ``embeddedCliPrint`` payload is terminated by ``\\r\\n``.

The target firmware in this project disables autocompletion
(``cli_config.enableAutoComplete = false`` in ``main.c``), so no
ghost-suggestion bytes are mixed into the echo stream.

Usage:
    python tests/uart/cli_commands_test.py --port /dev/cu.usbserialXXX \\
        --baudrate 115200

    # Optionally require an exact platform name in the `platform` reply:
    python tests/uart/cli_commands_test.py --port /dev/ttyUSB0 \\
        --expected-platform STM32F401
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field

try:
    import serial
except ImportError:
    print(
        "Error: pyserial is required. Install with: pip install pyserial",
        file=sys.stderr,
    )
    sys.exit(2)


# ── Protocol constants ───────────────────────────────────────────────

# Default invitation set by embeddedCliDefaultConfig() in
# firmware/external/embedded-cli/lib/src/embedded_cli.c.
DEFAULT_PROMPT = "> "

# Line break emitted by the embedded CLI after each printed line and after
# each accepted command line.
LINE_BREAK = "\r\n"

# Substring printed by onUnknownCommand() in embedded_cli.c.
UNKNOWN_COMMAND_MARKER = "Unknown command:"

# Help strings registered in firmware/application/main.c.
ID_HELP_TEXT = "Print unique platform ID"
PLATFORM_HELP_TEXT = "Print general platform info"
# Built-in help binding registered by initInternalBindings() in
# firmware/external/embedded-cli/lib/src/embedded_cli.c.
HELP_HELP_TEXT = "Print list of commands"

# Commands the target firmware registers in main.c (plus the built-in
# `help`). Order matches the registration order; the `help` listing in
# the embedded CLI prints registered commands in registration order, so
# we don't rely on any specific ordering in the test.
REGISTERED_COMMANDS = ("id", "platform", "help")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class CliTestConfig:
    """Configuration for a CLI command test run.

    Attributes:
        port: Serial port path of the CLI UART (e.g. /dev/cu.usbserial...,
            /dev/ttyUSB0). Distinct from the target-UART bridge port used
            by ``uart_echo_test.py``.
        baudrate: Baud rate of the CLI UART, in bits per second. Must
            match ``CONFIG_TEST_UART_BAUDRATE`` in the firmware build
            (default ``115200``).
        prompt: CLI invitation string. Match what was passed to
            ``embeddedCliNew`` on the target (default ``"> "``).
        warmup_sec: Delay after opening the port before sending the
            first byte, to let the UART line settle.
        per_cmd_timeout: Maximum time to wait for a single command to
            complete (i.e. for the next prompt to come back).
        expected_platform: Optional substring that must appear in the
            `platform` command reply. When ``None``, only the
            ``"Platform: "`` prefix is checked.
    """

    port: str
    baudrate: int = 115200
    prompt: str = DEFAULT_PROMPT
    warmup_sec: float = 0.3
    per_cmd_timeout: float = 2.0
    expected_platform: str | None = None


# ── Exceptions ───────────────────────────────────────────────────────


class CliTestError(Exception):
    """Base exception for CLI command test failures."""


class CliTimeoutError(CliTestError):
    """The target did not reach the next prompt within the timeout."""


class CliMismatchError(CliTestError):
    """A command reply did not match expectations."""


# ── Test result tracking ─────────────────────────────────────────────


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class TestReport:
    cases: list[CaseResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.cases.append(CaseResult(name=name, ok=ok, detail=detail))

    @property
    def all_passed(self) -> bool:
        return all(c.ok for c in self.cases)

    def print_summary(self) -> None:
        print("\n=== Summary ===")
        for c in self.cases:
            mark = "OK  " if c.ok else "FAIL"
            line = f"  [{mark}] {c.name}"
            if c.detail:
                line += f" -- {c.detail}"
            print(line)
        passed = sum(1 for c in self.cases if c.ok)
        print(f"\n{passed}/{len(self.cases)} passed")


# ── Low-level CLI I/O ────────────────────────────────────────────────


def _read_until(
    ser: serial.Serial, marker: bytes, timeout: float,
) -> bytes:
    """Read from *ser* until *marker* appears at the end of the stream.

    Returns the raw bytes accumulated, including the trailing *marker*.
    A single overall deadline of *timeout* seconds applies from the call
    — the timer is NOT reset on incoming bytes; the per-`ser.read` step
    is capped only so the loop can observe the deadline.
    """
    received = bytearray()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CliTimeoutError(
                f"Timed out waiting for {marker!r}; received={bytes(received)!r}"
            )
        ser.timeout = min(remaining, 0.2)
        chunk = ser.read(256)
        if chunk:
            received += chunk
            if received.endswith(marker):
                return bytes(received)


def _sync_to_prompt(
    ser: serial.Serial, prompt: str, timeout: float = 2.0,
) -> None:
    """Bring the CLI into a known idle state with a fresh prompt printed.

    Sends a single ``\\r`` (an empty command, which the CLI silently
    ignores apart from echoing CRLF + prompt) and discards everything
    received up to and including the next prompt. Any stale output that
    was already in flight before this call is drained.
    """
    ser.reset_input_buffer()
    ser.write(b"\r")
    ser.flush()
    _read_until(ser, prompt.encode("utf-8"), timeout=timeout)


def _send_command(
    ser: serial.Serial,
    command: str,
    prompt: str,
    timeout: float,
) -> str:
    """Send *command* + CR and return the parsed reply body.

    The reply body is the text printed between the echoed command line
    and the next prompt. Specifically the function:

      1. Writes ``command + "\\r"``.
      2. Reads bytes until the next *prompt* is observed at the tail.
      3. Strips the leading echo ``command + "\\r\\n"`` (if present).
      4. Strips the trailing prompt.

    The returned string still contains any embedded CR/LF emitted by
    the command handler, so callers can split on ``"\\r\\n"`` when they
    need to inspect individual lines.
    """
    encoded = (command + "\r").encode("utf-8")
    ser.write(encoded)
    ser.flush()

    raw = _read_until(ser, prompt.encode("utf-8"), timeout=timeout)
    text = raw.decode("utf-8", errors="replace")

    echoed = command + LINE_BREAK
    if text.startswith(echoed):
        text = text[len(echoed):]
    if text.endswith(prompt):
        text = text[: -len(prompt)]
    return text


# ── Individual command tests ─────────────────────────────────────────


def _check_id_reply(reply: str) -> str:
    """Validate the `id` command reply and return the trimmed ID string."""
    body = reply.strip()
    if not body:
        raise CliMismatchError("`id` returned an empty body")
    # Expect a single line of output.
    if LINE_BREAK in body:
        raise CliMismatchError(
            f"`id` should print one line, got multi-line: {body!r}"
        )
    # platform_get_id() returns hex (per platform.c snprintf "%08lX..."),
    # but the exact length varies per MCU family. Stay permissive: any
    # printable, non-whitespace token is accepted.
    if any(ch.isspace() for ch in body):
        raise CliMismatchError(
            f"`id` reply contains whitespace: {body!r}"
        )
    return body


def _check_platform_reply(
    reply: str, expected_substr: str | None,
) -> str:
    """Validate the `platform` command reply and return the platform name."""
    body = reply.strip()
    m = re.match(r"^Platform:\s+(.+)$", body)
    if m is None:
        raise CliMismatchError(
            f"`platform` reply does not start with 'Platform: ': {body!r}"
        )
    name = m.group(1).strip()
    if not name:
        raise CliMismatchError(
            f"`platform` reply has an empty platform name: {body!r}"
        )
    if expected_substr is not None and expected_substr not in name:
        raise CliMismatchError(
            f"`platform` reply {name!r} does not contain expected substring "
            f"{expected_substr!r}"
        )
    return name


def _check_help_list(reply: str) -> None:
    """Validate that `help` (no args) lists every registered command.

    onHelp() in embedded_cli.c prints one entry per binding as
    ``" * <name>\\r\\n\\t<help-text>\\r\\n"``. We don't rely on bullet
    formatting beyond the command name and its help text appearing in
    the reply.
    """
    for cmd in REGISTERED_COMMANDS:
        if cmd not in reply:
            raise CliMismatchError(
                f"`help` listing is missing command {cmd!r}; reply={reply!r}"
            )
    # Spot-check help text fragments too — guards against the firmware
    # registering the binding but losing the help string.
    for fragment in (ID_HELP_TEXT, PLATFORM_HELP_TEXT, HELP_HELP_TEXT):
        if fragment not in reply:
            raise CliMismatchError(
                f"`help` listing is missing help text {fragment!r}; reply={reply!r}"
            )


def _check_help_for(reply: str, name: str, expected_help: str) -> None:
    """Validate `help <name>` lists the command and its help text."""
    if name not in reply:
        raise CliMismatchError(
            f"`help {name}` reply omits the command name; reply={reply!r}"
        )
    if expected_help not in reply:
        raise CliMismatchError(
            f"`help {name}` reply omits expected help text {expected_help!r}; "
            f"reply={reply!r}"
        )


def _check_unknown_command(reply: str, name: str) -> None:
    if UNKNOWN_COMMAND_MARKER not in reply:
        raise CliMismatchError(
            f"unknown-command reply missing {UNKNOWN_COMMAND_MARKER!r}; "
            f"reply={reply!r}"
        )
    if name not in reply:
        raise CliMismatchError(
            f"unknown-command reply does not quote the offending name "
            f"{name!r}; reply={reply!r}"
        )


def _check_empty_command(reply: str) -> None:
    """Validate that sending only ``\\r`` produces no command output.

    parseCommand() in embedded_cli.c short-circuits on an empty/whitespace
    buffer, so the only bytes between the sent ``\\r`` and the next
    prompt are the CRLF that ``onControlInput`` always prints.
    """
    if reply not in ("", LINE_BREAK, "\n"):
        raise CliMismatchError(
            f"empty command should produce no body, got {reply!r}"
        )


# ── Orchestration ───────────────────────────────────────────────────


def run_cli_tests(cfg: CliTestConfig) -> TestReport:
    """Run every CLI command test against the target.

    Returns a populated :class:`TestReport`. Does not raise on individual
    case failures — those are recorded in the report. Raises only on
    fatal I/O errors (serial port failure, total timeout while syncing).
    """
    report = TestReport()
    print(
        f"Opening {cfg.port} @ {cfg.baudrate} 8N1, prompt={cfg.prompt!r}"
    )

    with serial.Serial(
        port=cfg.port,
        baudrate=cfg.baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=cfg.per_cmd_timeout,
    ) as ser:
        if cfg.warmup_sec > 0:
            time.sleep(cfg.warmup_sec)
        _sync_to_prompt(ser, cfg.prompt, timeout=cfg.per_cmd_timeout)

        # 1) `id`
        try:
            reply = _send_command(ser, "id", cfg.prompt, cfg.per_cmd_timeout)
            ident = _check_id_reply(reply)
            report.add("id", True, f"id={ident!r}")
            print(f"[OK] id -> {ident!r}")
        except CliTestError as e:
            report.add("id", False, str(e))
            print(f"[FAIL] id: {e}")

        # 2) `platform`
        try:
            reply = _send_command(
                ser, "platform", cfg.prompt, cfg.per_cmd_timeout
            )
            name = _check_platform_reply(reply, cfg.expected_platform)
            report.add("platform", True, f"name={name!r}")
            print(f"[OK] platform -> {name!r}")
        except CliTestError as e:
            report.add("platform", False, str(e))
            print(f"[FAIL] platform: {e}")

        # 3) `help` (lists all bindings)
        try:
            reply = _send_command(
                ser, "help", cfg.prompt, cfg.per_cmd_timeout
            )
            _check_help_list(reply)
            report.add("help", True)
            print("[OK] help (lists all commands)")
        except CliTestError as e:
            report.add("help", False, str(e))
            print(f"[FAIL] help: {e}")

        # 4) `help id`
        try:
            reply = _send_command(
                ser, "help id", cfg.prompt, cfg.per_cmd_timeout
            )
            _check_help_for(reply, "id", ID_HELP_TEXT)
            report.add("help id", True)
            print("[OK] help id")
        except CliTestError as e:
            report.add("help id", False, str(e))
            print(f"[FAIL] help id: {e}")

        # 5) `help platform`
        try:
            reply = _send_command(
                ser, "help platform", cfg.prompt, cfg.per_cmd_timeout
            )
            _check_help_for(reply, "platform", PLATFORM_HELP_TEXT)
            report.add("help platform", True)
            print("[OK] help platform")
        except CliTestError as e:
            report.add("help platform", False, str(e))
            print(f"[FAIL] help platform: {e}")

        # 6) `help help` (built-in binding)
        try:
            reply = _send_command(
                ser, "help help", cfg.prompt, cfg.per_cmd_timeout
            )
            _check_help_for(reply, "help", HELP_HELP_TEXT)
            report.add("help help", True)
            print("[OK] help help")
        except CliTestError as e:
            report.add("help help", False, str(e))
            print(f"[FAIL] help help: {e}")

        # 7) Unknown command
        try:
            bogus = "this_command_does_not_exist"
            reply = _send_command(
                ser, bogus, cfg.prompt, cfg.per_cmd_timeout
            )
            _check_unknown_command(reply, bogus)
            report.add("unknown command", True)
            print("[OK] unknown command")
        except CliTestError as e:
            report.add("unknown command", False, str(e))
            print(f"[FAIL] unknown command: {e}")

        # 8) Empty input (bare CR)
        try:
            reply = _send_command(ser, "", cfg.prompt, cfg.per_cmd_timeout)
            _check_empty_command(reply)
            report.add("empty input", True)
            print("[OK] empty input (no command body)")
        except CliTestError as e:
            report.add("empty input", False, str(e))
            print(f"[FAIL] empty input: {e}")

    return report


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
        raise argparse.ArgumentTypeError(f"must be >= 0, got {x}")
    return x


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the MioLink test firmware embedded CLI over UART. "
            "The firmware must be built with CONFIG_CLI_USE_UART so the "
            "CLI is exposed on the platform UART pins."
        ),
    )
    parser.add_argument(
        "--port", required=True,
        help=(
            "Serial port path of the CLI UART (USB-UART adapter wired to "
            "the target's CLI UART pins). NOT the MioLink target-UART "
            "bridge port used by uart_echo_test.py."
        ),
    )
    parser.add_argument(
        "--baudrate", type=_positive_int, default=115200,
        help=(
            "Baud rate (default: 115200). Must match "
            "CONFIG_TEST_UART_BAUDRATE in the firmware build."
        ),
    )
    parser.add_argument(
        "--prompt", default=DEFAULT_PROMPT,
        help=(
            "Expected CLI invitation string (default: %(default)r). "
            "Override only if the firmware passes a custom invitation to "
            "embeddedCliNew()."
        ),
    )
    parser.add_argument(
        "--warmup", type=_non_negative_float, default=0.3, dest="warmup_sec",
        help="Delay in seconds after opening the port (default: 0.3)",
    )
    parser.add_argument(
        "--timeout", type=_positive_float, default=2.0, dest="per_cmd_timeout",
        help=(
            "Per-command timeout in seconds — maximum time to wait for the "
            "next prompt after sending a command (default: 2.0)"
        ),
    )
    parser.add_argument(
        "--expected-platform", default=None,
        help=(
            "Optional substring that must appear in the `platform` "
            "command reply (e.g. STM32F401). If omitted, only the "
            "'Platform: ' prefix is checked."
        ),
    )

    args = parser.parse_args()

    cfg = CliTestConfig(
        port=args.port,
        baudrate=args.baudrate,
        prompt=args.prompt,
        warmup_sec=args.warmup_sec,
        per_cmd_timeout=args.per_cmd_timeout,
        expected_platform=args.expected_platform,
    )

    try:
        report = run_cli_tests(cfg)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except CliTimeoutError as e:
        print(f"FAIL (fatal timeout): {e}", file=sys.stderr)
        return 1
    except serial.SerialException as e:
        print(f"Serial error: {e}", file=sys.stderr)
        return 2

    report.print_summary()
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

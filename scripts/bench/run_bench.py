#!/usr/bin/env python3
"""Run the MioLink_TestBench test bench from a YAML configuration.

Reads a bench configuration (default: ``config/bench_pi5.yaml``), and for
each probe / target connection on the bench:

  1. Selects every firmware variant to exercise — Debug and Release for
     the probe's individual ``build_target``, plus Debug and Release of
     the ``auto_rp2040`` multi-board build when the probe is supported by
     it (Pico, Pico W, MioLink rev. A/B, MioLink_Pico).
  2. Flashes the corresponding ``MioLink.uf2`` to the probe over DFU
     runtime + RP2040 MSC and waits for re-enumeration.
  3. Verifies probe identity before running tests by reading the USB
     iProduct descriptor AND parsing ``monitor version``: the reported
     board ident must match the variant (e.g. ``auto_rp2040`` reports the
     detected board name; the dedicated ``miolink`` build reports
     ``MioLink``; dedicated Pico builds report the raw ``PICO_BOARD``
     value such as ``pico_w``). The ``Hardware Version N`` field from
     ``monitor version`` is compared against the YAML
     ``hardware_version`` field. The Debug-build marker is required for
     Debug variants and forbidden for Release variants.
  4. Runs the smoke buffer tests (verify_only + write_and_verify) over
     each debug interface that the slot wires up (SWD and/or JTAG).

The script reuses sibling packages from ``scripts/``: ``gdb_bmp``,
``miolink``, ``tests.buffer.bmp_buffer_test``, ``usb_helpers``. It does
not flash the target — the target is expected to already run the
``test_board_<platform>`` fixture firmware built from this repository
(see ``<target_build_dir>/test_boards/test_board_<platform>/`` after
``cmake --build``, where ``<target_build_dir>`` is passed via
``--target-build-dir``).

The .uf2 probe images are located under ``--probe-image-dir`` by the
fixed name ``MioLink-<board>-<flavour>.uf2``, where ``<board>`` is the
``build_target`` (or ``auto_rp2040``) and ``<flavour>`` is ``debug`` /
``release``.

Example:
    python scripts/bench/run_bench.py \\
        --config config/bench_pi5.yaml \\
        --probe-image-dir /path/to/probe-images
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

# Sibling-package imports: run_bench lives in scripts/bench/ and needs
# scripts/ on sys.path to import miolink/, gdb_bmp/, usb_helpers/, tests/,
# and scripts/bench/ itself for the sibling bench_config module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.buffer import bmp_buffer_test
import gdb_bmp
import miolink
from usb_helpers import find_device

# Bench YAML schema and firmware-variant derivation shared with the build
# helpers (build_miolink.py / build_all_test_firmware.py).
from bench_config import (
    _AUTO_BUILD_TARGET,
    BenchConfig,
    BenchConnection,
    BuildVariant,
    load_bench_config,
    resolve_uf2,
    target_build_subdir,
    variants_for,
)

# ── Constants ────────────────────────────────────────────────────────

_MIOLINK_VID = 0x1D50
_MIOLINK_PID = 0x6018

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Buffer-fixture details mirrored from
# MioLink_TestBench/application/main.c. Keep in sync with the C source if
# CONFIG_TEST_BUFFER_SIZE or the test strings change.
_BUFFER_SIZE = 256

_BUFFER_ALWAYS_SAME_NAME = "test_buffer_always_same"
_BUFFER_ALWAYS_SAME_STRING = (
    "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
    "nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in "
    "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur."
)

_BUFFER_OVERWRITE_NAME = "test_buffer_to_overwrite"
_BUFFER_OVERWRITE_PREFIX = "MioLink Testing, platform: "
_BUFFER_OVERWRITE_SUFFIX = (
    ".Buffer Overwrite Test: Lorem ipsum dolor sit amet, consectetur "
    "adipiscing elit."
)

# ── Expected-probe matching ──────────────────────────────────────────


def expected_ident_token(connection: BenchConnection, variant: BuildVariant) -> str:
    """Return the probe-ident substring expected from ``monitor version``.

    The ident reported by the firmware depends on the build:

    * ``auto_rp2040`` reports the detected board name — ``MioLink``,
      ``MioLink_Pico``, ``Pico``, or ``Pico W``.
    * The dedicated ``miolink`` build always reports ``MioLink`` (both
      rev. A and rev. B share the same firmware).
    * The dedicated ``miolink_pico`` build reports ``MioLink_Pico``.
    * Other dedicated Pico-SDK builds (``pico``, ``pico_w``, ``pico2_w``,
      …) report the raw ``PICO_BOARD`` value via
      ``BOARD_IDENT_NON_MIOLINK``.
    """
    if variant.board == _AUTO_BUILD_TARGET:
        if connection.probe.lower().startswith("miolink rev"):
            return "MioLink"
        return connection.probe
    if variant.board == "miolink":
        return "MioLink"
    if variant.board == "miolink_pico":
        return "MioLink_Pico"
    return variant.board


def _extract_ident_segments(text: str) -> list[str] | None:
    """Return the comma-separated segments of the bracketed probe ident.

    The probe firmware advertises its board ident inside parentheses
    in both the USB iProduct descriptor and the first line of
    ``monitor version`` output, e.g.::

        Black Magic Probe (MioLink, auto-detect, Debug Build) v1.10.2

    The extra suffix after the board name comes from
    ``PLATFORM_IDENT_EXTRA_INFO``: it carries ``auto-detect`` for the
    multi-board build and ``Debug Build`` for Debug builds. Returns
    ``None`` when no parenthesised ident is found.
    """
    match = re.search(r"\(([^)]+)\)", text)
    if match is None:
        return None
    return [segment.strip() for segment in match.group(1).split(",")]


def _ident_matches(text: str, token: str) -> bool:
    """Return True iff the first segment of *text*'s bracketed ident equals *token*.

    Case-insensitive exact comparison on the first comma-separated
    segment. This avoids false positives such as ``Pico`` matching
    a ``Pico W`` probe.
    """
    segments = _extract_ident_segments(text)
    if not segments:
        return False
    return segments[0].lower() == token.lower()


def _parse_hw_version(version_text: str) -> int | None:
    match = re.search(r"Hardware Version\s+(-?\d+)", version_text)
    if match is None:
        return None
    return int(match.group(1))


# ── USB helpers ──────────────────────────────────────────────────────


def _get_usb_iproduct(serial: str) -> str:
    """Read the iProduct string of the MioLink USB device by *serial*."""
    import usb.core
    import usb.util

    backend = find_device._get_usb_backend()  # noqa: SLF001 — internal use
    devices = list(usb.core.find(
        find_all=True,
        idVendor=_MIOLINK_VID,
        idProduct=_MIOLINK_PID,
        backend=backend,
    ))
    for dev in devices:
        try:
            dev_serial = usb.util.get_string(dev, dev.iSerialNumber)
        except (usb.core.USBError, ValueError):
            continue
        if dev_serial != serial:
            continue
        try:
            return usb.util.get_string(dev, dev.iProduct) or ""
        except (usb.core.USBError, ValueError):
            return ""
    raise find_device.DeviceNotFoundError(
        f"No MioLink with serial '{serial}' on the bus"
    )


def _wait_for_reappear(
    serial: str,
    timeout_sec: float,
    poll_interval_sec: float = 0.5,
) -> None:
    """Poll until the MioLink with *serial* re-enumerates, or fail."""
    deadline = time.monotonic() + timeout_sec
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            find_device.find_device_address(
                vid=_MIOLINK_VID, pid=_MIOLINK_PID, serial=serial,
            )
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(poll_interval_sec)
    raise TimeoutError(
        f"MioLink probe (serial={serial}) did not reappear within "
        f"{timeout_sec:.1f}s"
        + (f": last error was {last_exc}" if last_exc is not None else "")
    )


# ── Monitor version (probe-only, no scan) ────────────────────────────


def _run_probe_monitor(
    gdb_port: str, gdb_executable: str, monitor_command: str,
    timeout_sec: float = 10.0,
) -> str:
    """Run a single ``monitor`` command against the probe, no scan or attach.

    Returns the joined ``target``/``console`` stream output (stripped of
    blank lines).
    """
    import shutil

    from pygdbmi.gdbcontroller import GdbController

    if not shutil.which(gdb_executable):
        raise FileNotFoundError(
            f"GDB executable not found: {gdb_executable}"
        )

    gdb = GdbController(command=[
        gdb_executable, "--nx", "--quiet", "--interpreter=mi3",
    ])
    try:
        gdb.write(
            "-gdb-set mi-async on", timeout_sec=5,
            raise_error_on_timeout=False,
        )
        responses = gdb.write(
            f"target extended-remote {gdb_port}", timeout_sec=10,
        )
        for resp in responses:
            if (resp.get("type") == "result"
                    and resp.get("message") == "error"):
                payload = resp.get("payload") or {}
                msg = (
                    payload.get("msg", str(payload))
                    if isinstance(payload, dict) else str(payload)
                )
                raise RuntimeError(
                    f"cannot connect to BMP at {gdb_port}: {msg}"
                )

        responses = gdb.write(
            f"monitor {monitor_command}", timeout_sec=timeout_sec,
            raise_error_on_timeout=False,
        )
        deadline = time.monotonic() + timeout_sec
        while not any(r.get("type") == "result" for r in responses):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            extra = gdb.get_gdb_response(
                timeout_sec=min(2.0, remaining),
                raise_error_on_timeout=False,
            )
            if extra:
                responses.extend(extra)

        lines: list[str] = []
        for resp in responses:
            if resp.get("type") in ("target", "console"):
                payload = resp.get("payload") or ""
                lines.extend(payload.splitlines())
        return "\n".join(line for line in lines if line.strip())
    finally:
        try:
            gdb.write(
                "-gdb-exit", timeout_sec=2,
                raise_error_on_timeout=False,
            )
        except Exception:
            pass
        try:
            gdb.exit()
        except Exception:
            pass


def _query_probe_version(gdb_port: str, gdb_executable: str) -> str:
    """Run ``monitor version`` against the probe and return its full output."""
    return _run_probe_monitor(gdb_port, gdb_executable, "version")


# ── Verification ─────────────────────────────────────────────────────


@dataclass
class VerificationFailure:
    """Single mismatch between expected probe identity and what was observed."""

    where: str
    detail: str


def verify_probe_identity(
    connection: BenchConnection,
    variant: BuildVariant,
    iproduct: str,
    version_text: str,
) -> list[VerificationFailure]:
    """Compare *iproduct* + *version_text* against the expected variant.

    Returns a list of failures (empty on success).
    """
    failures: list[VerificationFailure] = []
    expected = expected_ident_token(connection, variant)

    if iproduct and not _ident_matches(iproduct, expected):
        failures.append(VerificationFailure(
            where="USB iProduct",
            detail=(
                f"expected ident '{expected}' not in iProduct "
                f"'{iproduct}'"
            ),
        ))

    if version_text:
        if not _ident_matches(version_text, expected):
            failures.append(VerificationFailure(
                where="monitor version",
                detail=(
                    f"expected ident '{expected}' not in version output"
                ),
            ))

        # The multi-board auto build appends "auto-detect" to the
        # parenthesised ident via PLATFORM_IDENT_EXTRA_INFO. This lets
        # us distinguish a Pico/Pico W auto build (which reports the
        # detected board name "Pico"/"Pico W") from the dedicated
        # pico/pico_w build (which reports the raw PICO_BOARD value):
        # the two only differ by case otherwise.
        has_auto = "auto-detect" in version_text.lower()
        wants_auto = variant.board == _AUTO_BUILD_TARGET
        if has_auto != wants_auto:
            want = "auto-detect build" if wants_auto else "dedicated build"
            actual = "auto-detect" if has_auto else "dedicated"
            failures.append(VerificationFailure(
                where="monitor version",
                detail=f"expected {want} but probe reports {actual}",
            ))

        has_debug = "debug build" in version_text.lower()
        wants_debug = variant.flavour == "debug"
        if has_debug != wants_debug:
            want = "Debug Build" if wants_debug else "Release"
            actual = "Debug Build" if has_debug else "Release"
            failures.append(VerificationFailure(
                where="monitor version",
                detail=(
                    f"expected {want} but probe reports {actual}"
                ),
            ))

        if connection.hardware_version is not None:
            reported = _parse_hw_version(version_text)
            if reported is None:
                failures.append(VerificationFailure(
                    where="monitor version",
                    detail="could not parse 'Hardware Version N'",
                ))
            elif reported != connection.hardware_version:
                failures.append(VerificationFailure(
                    where="monitor version",
                    detail=(
                        f"expected Hardware Version "
                        f"{connection.hardware_version}, probe reports "
                        f"{reported}"
                    ),
                ))

    return failures


# ── Buffer-test expected payloads ────────────────────────────────────


def _bytes_padded(text: str) -> bytes:
    raw = text.encode("ascii") + b"\x00"
    if len(raw) > _BUFFER_SIZE:
        raise ValueError(
            f"string is {len(raw)} bytes, exceeds buffer size {_BUFFER_SIZE}"
        )
    return raw + b"\x00" * (_BUFFER_SIZE - len(raw))


def _expected_always_same() -> bytes:
    return _bytes_padded(_BUFFER_ALWAYS_SAME_STRING)


def _expected_overwrite(platform_name_string: str) -> bytes:
    return _bytes_padded(
        _BUFFER_OVERWRITE_PREFIX + platform_name_string + _BUFFER_OVERWRITE_SUFFIX,
    )


def _target_elf_path(
    connection: BenchConnection, build_dir: Path, cli_mode: str,
) -> Path:
    name = f"test_board_{connection.platform}"
    sub = target_build_subdir(cli_mode, connection.swo)
    return build_dir / sub / "test_boards" / name / f"{name}.elf"


def _make_target_config(
    connection: BenchConnection,
    gdb_port: str,
    elf_path: Path,
    iface: str,
    gdb_executable: str,
) -> gdb_bmp.BmpTargetConfig:
    """Build a :class:`gdb_bmp.BmpTargetConfig` from per-connection YAML."""
    kwargs: dict[str, object] = dict(
        gdb_port=gdb_port,
        elf_path=elf_path,
        target_name=connection.gdb_scan_name,
        interface=gdb_bmp.ScanInterface(iface),
        connect_under_reset=connection.reset_connected,
        gdb_executable=gdb_executable,
        expected_vtref=connection.vtref_voltage,
        frequency_hz=connection.freq_hz,
    )
    if connection.vtref_tolerance is not None:
        kwargs["vtref_tolerance"] = connection.vtref_tolerance
    return gdb_bmp.BmpTargetConfig(**kwargs)


# ── Test runner ──────────────────────────────────────────────────────


@dataclass
class VariantResult:
    """Outcome of testing a single (probe variant, target cli) combination."""

    connection: BenchConnection
    variant: BuildVariant
    cli_mode: str
    steps_passed: list[str] = field(default_factory=list)
    steps_failed: list[tuple[str, str]] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def passed(self) -> bool:
        return not self.steps_failed and self.skipped_reason is None

    @property
    def label(self) -> str:
        return f"{self.variant.label} cli/{self.cli_mode}"


def _run_variant(
    connection: BenchConnection,
    variant: BuildVariant,
    cli_mode: str,
    uf2_path: Path,
    args: argparse.Namespace,
) -> VariantResult:
    result = VariantResult(
        connection=connection, variant=variant, cli_mode=cli_mode,
    )
    banner = (
        f"\n========== {connection.probe} (sn={connection.serial}) "
        f":: {result.label} =========="
    )
    print(banner)

    if not uf2_path.is_file():
        if args.skip_missing_uf2:
            print(f"[skip] firmware not found: {uf2_path}")
            result.skipped_reason = f"firmware not found: {uf2_path}"
            return result
        result.steps_failed.append((
            "locate firmware",
            f"firmware not found: {uf2_path}",
        ))
        return result

    # ── Step 1: flash probe ───────────────────────────────────────
    label = f"flash MioLink probe ({uf2_path.name})"
    print(f"[step] {label} ...")
    try:
        miolink.flash_miolink(
            serial=connection.serial,
            uf2_path=uf2_path,
            bootsel_timeout_sec=args.miolink_flash_timeout,
        )
    except Exception as exc:
        msg = str(exc)
        print(f"FAIL {label}: {msg}", file=sys.stderr)
        result.steps_failed.append((label, msg))
        return result
    print(f"[ ok ] {label}")
    result.steps_passed.append(label)

    # ── Step 2: wait for re-enumeration ───────────────────────────
    print(
        f"[info] waiting up to {args.miolink_reappear_timeout:.0f}s "
        f"for probe to re-enumerate ..."
    )
    try:
        _wait_for_reappear(
            serial=connection.serial,
            timeout_sec=args.miolink_reappear_timeout,
        )
    except TimeoutError as exc:
        print(f"FAIL probe re-enumeration: {exc}", file=sys.stderr)
        result.steps_failed.append(("probe re-enumeration", str(exc)))
        return result

    if args.miolink_settle_sec > 0:
        time.sleep(args.miolink_settle_sec)

    # ── Step 3: verify probe identity ─────────────────────────────
    label = "verify probe identity"
    print(f"[step] {label} ...")

    iproduct = ""
    try:
        iproduct = _get_usb_iproduct(connection.serial)
        print(f"         iProduct: {iproduct}")
    except Exception as exc:
        print(f"[warn] could not read USB iProduct: {exc}")

    try:
        address = find_device.find_device_address(
            vid=_MIOLINK_VID, pid=_MIOLINK_PID, serial=connection.serial,
        )
        ports = miolink.find_serial_ports(address)
    except Exception as exc:
        msg = f"cannot resolve GDB port: {exc}"
        print(f"FAIL {label}: {msg}", file=sys.stderr)
        result.steps_failed.append((label, msg))
        return result
    print(f"         GDB port: {ports.gdb}")

    try:
        version_text = _query_probe_version(ports.gdb, args.gdb_executable)
    except Exception as exc:
        msg = f"monitor version failed: {exc}"
        print(f"FAIL {label}: {msg}", file=sys.stderr)
        result.steps_failed.append((label, msg))
        return result
    if version_text:
        print(textwrap.indent(version_text, "         "))

    failures = verify_probe_identity(
        connection, variant, iproduct, version_text,
    )
    if failures:
        for f in failures:
            print(f"FAIL {label}: [{f.where}] {f.detail}", file=sys.stderr)
            result.steps_failed.append((label, f"[{f.where}] {f.detail}"))
        return result
    print(f"[ ok ] {label}")
    result.steps_passed.append(label)

    # ── Step 3b: enable target power on probes that source VTref ──
    # Probes whose YAML 'vtref' is 'powered' supply the target rail via
    # VTref; without `monitor tpwr enable` after a fresh probe flash the
    # rail stays off, scan sees 0V, and SWD/JTAG fail. Run once per
    # variant because probe firmware re-flashing resets tpwr to off.
    if connection.vtref == "powered":
        label = "enable target power (tpwr)"
        print(f"[step] {label} ...")
        try:
            tpwr_text = _run_probe_monitor(
                ports.gdb, args.gdb_executable, "tpwr enable",
            )
        except Exception as exc:
            msg = f"monitor tpwr enable failed: {exc}"
            print(f"FAIL {label}: {msg}", file=sys.stderr)
            result.steps_failed.append((label, msg))
            return result
        if tpwr_text:
            print(textwrap.indent(tpwr_text, "         "))
        if connection.tpwr_settle_sec and connection.tpwr_settle_sec > 0:
            time.sleep(connection.tpwr_settle_sec)
        print(f"[ ok ] {label}")
        result.steps_passed.append(label)

    # ── Step 4: buffer tests per debug interface ──────────────────
    elf_path = _target_elf_path(connection, args.target_build_dir, cli_mode)
    if not elf_path.is_file():
        msg = (
            f"target ELF not found: {elf_path}. Build the "
            f"test_board_{connection.platform} target first."
        )
        print(f"FAIL locate target ELF: {msg}", file=sys.stderr)
        result.steps_failed.append(("locate target ELF", msg))
        return result

    expected_always = _expected_always_same()
    expected_overwrite = _expected_overwrite(connection.platform)

    valid_ifaces = [i for i in connection.interfaces if i in ("swd", "jtag")]

    # ── Step 4: flash target with the test_board fixture ──────────
    # The buffer tests assume the target already runs
    # `test_board_<platform>` (with the expected buffers in .data /
    # .bss). We flash once per (probe, variant): once the target image
    # is on the MCU it persists across the SWD/JTAG buffer tests below.
    if not args.no_target_flash and valid_ifaces:
        flash_iface = "swd" if "swd" in valid_ifaces else valid_ifaces[0]
        label = f"flash target {elf_path.name} via {flash_iface}"
        print(f"[step] {label} ...")
        flash_target_config = _make_target_config(
            connection, ports.gdb, elf_path, flash_iface,
            args.gdb_executable,
        )
        flash_cfg = gdb_bmp.BmpFlashConfig(
            mass_erase=not args.no_target_mass_erase,
            verify=not args.no_target_verify,
        )
        try:
            gdb_bmp.flash_target(flash_target_config, flash_cfg)
        except Exception as exc:
            msg = str(exc)
            print(f"FAIL {label}: {msg}", file=sys.stderr)
            result.steps_failed.append((label, msg))
            # Target flash failed — buffer tests can't pass without the
            # fixture firmware on the target, so skip the rest of this
            # variant.
            return result
        print(f"[ ok ] {label}")
        result.steps_passed.append(label)

    for iface in valid_ifaces:
        target_config = _make_target_config(
            connection, ports.gdb, elf_path, iface, args.gdb_executable,
        )

        for buf_name, expected, test_type in (
            (
                _BUFFER_ALWAYS_SAME_NAME, expected_always,
                bmp_buffer_test.TestType.VERIFY_ONLY,
            ),
            (
                _BUFFER_OVERWRITE_NAME, expected_overwrite,
                bmp_buffer_test.TestType.WRITE_AND_VERIFY,
            ),
        ):
            label = f"{iface}/{test_type.value} {buf_name}"
            print(f"[step] {label} ...")
            test_config = bmp_buffer_test.BufferTestConfig(
                buffer_name=buf_name,
                expected_value=expected,
                test_type=test_type,
                run_time_sec=args.run_time,
            )
            try:
                bmp_buffer_test.run_buffer_test(target_config, test_config)
            except Exception as exc:
                msg = str(exc)
                print(f"FAIL {label}: {msg}", file=sys.stderr)
                result.steps_failed.append((label, msg))
                continue
            print(f"[ ok ] {label}")
            result.steps_passed.append(label)

    return result


# ── CLI ──────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MioLink_TestBench bench from a YAML configuration."
        ),
    )
    parser.add_argument(
        "--config", type=Path,
        default=_REPO_ROOT / "config" / "bench_pi5.yaml",
        help=(
            "Path to the bench YAML config "
            "(default: config/bench_pi5.yaml)."
        ),
    )
    parser.add_argument(
        "--probe-image-dir", type=Path, required=True,
        help=(
            "Staging directory containing the MioLink probe .uf2 images "
            "for every (board, flavour) variant referenced by the bench."
        ),
    )
    parser.add_argument(
        "--target-build-dir", type=Path, required=True,
        help=(
            "Directory containing the test_board_<platform>.elf "
            "fixtures (the CMake build dir for the target firmware, "
            "e.g. <firmware_root>/build)."
        ),
    )
    parser.add_argument(
        "--probe", type=str, default=None,
        help=(
            "Run only the probe whose USB serial matches this value. "
            "By default every probe in the config is tested."
        ),
    )
    parser.add_argument(
        "--include-disabled", action="store_true",
        help=(
            "Also run probes whose YAML 'enabled' field is false. "
            "By default disabled probes are skipped."
        ),
    )
    parser.add_argument(
        "--skip-missing-uf2", action="store_true",
        help=(
            "Skip variants whose .uf2 file does not exist instead of "
            "marking them as failed."
        ),
    )
    parser.add_argument(
        "--miolink-flash-timeout", type=float, default=30.0,
        help=(
            "Seconds to wait for the RP2 BOOTSEL device after DFU detach "
            "before picotool loads the image (default: 30)."
        ),
    )
    parser.add_argument(
        "--miolink-reappear-timeout", type=float, default=30.0,
        help=(
            "Seconds to wait for the MioLink probe to re-enumerate "
            "after MSC upload (default: 30)."
        ),
    )
    parser.add_argument(
        "--miolink-settle-sec", type=float, default=2.0,
        help=(
            "Extra seconds to wait after re-enumeration before opening "
            "the GDB port (default: 2)."
        ),
    )
    parser.add_argument(
        "--gdb-executable", default="arm-none-eabi-gdb",
        help="Path to arm-none-eabi-gdb (default: arm-none-eabi-gdb).",
    )
    parser.add_argument(
        "--run-time", type=float, default=3.0,
        help=(
            "Seconds to let the target run between buffer operations "
            "(default: 3)."
        ),
    )
    parser.add_argument(
        "--no-target-flash", action="store_true",
        help=(
            "Skip flashing the test_board fixture firmware to the "
            "target. The buffer tests assume the fixture image is "
            "already on the MCU."
        ),
    )
    parser.add_argument(
        "--no-target-mass-erase", action="store_true",
        help="Skip mass erase before flashing the target.",
    )
    parser.add_argument(
        "--no-target-verify", action="store_true",
        help="Skip post-flash compare-sections verify on the target.",
    )
    return parser


def _print_summary(results: list[VariantResult]) -> int:
    print("\n" + "=" * 70)
    print("Bench summary")
    print("=" * 70)

    total = len(results)
    failed = [r for r in results if not r.passed and r.skipped_reason is None]
    skipped = [r for r in results if r.skipped_reason is not None]
    passed = [r for r in results if r.passed]

    for r in results:
        marker = (
            "PASS" if r.passed
            else "SKIP" if r.skipped_reason is not None
            else "FAIL"
        )
        line = (
            f"  [{marker}] {r.connection.probe} (sn={r.connection.serial}) "
            f":: {r.label}"
        )
        if r.skipped_reason:
            line += f" — {r.skipped_reason}"
        print(line)
        for step, detail in r.steps_failed:
            print(f"           - {step}: {detail}")

    print(
        f"\nTotal: {total}   "
        f"Passed: {len(passed)}   "
        f"Failed: {len(failed)}   "
        f"Skipped: {len(skipped)}"
    )
    return 0 if not failed else 1


def main() -> int:
    args = _build_parser().parse_args()

    try:
        config = load_bench_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    results: list[VariantResult] = []
    for connection in config.connections:
        if args.probe is not None and connection.serial != args.probe:
            continue
        if not connection.enabled and not args.include_disabled:
            print(
                f"[skip] {connection.probe} (sn={connection.serial}) "
                f"is disabled in {args.config}"
            )
            continue
        for variant in variants_for(connection):
            uf2_path = resolve_uf2(
                args.probe_image_dir, variant,
            )
            for cli_mode in connection.cli:
                results.append(
                    _run_variant(connection, variant, cli_mode, uf2_path, args),
                )

    if not results:
        print(
            "error: no connections matched the filter; nothing to do",
            file=sys.stderr,
        )
        return 2

    return _print_summary(results)


if __name__ == "__main__":
    sys.exit(main())

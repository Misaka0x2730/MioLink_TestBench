"""Black Magic Probe target flasher module.

Connects to a Black Magic Probe via GDB/MI, scans for targets,
verifies the target name, and flashes an ELF firmware file.

Usage as module:
    from gdb_bmp import BmpTargetConfig, flash_target, BmpFlashConfig

    target = BmpTargetConfig(
        gdb_port="/dev/cu.usbmodem1134401",
        elf_path="firmware.elf",
        target_name="STM32F1",
    )
    flash_target(target, BmpFlashConfig(mass_erase=True))

Usage as CLI:
    python gdb_bmp/target_flash.py --port /dev/cu.usbmodem1134401 \\
        --interface swd --target STM32F1 firmware.elf
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow `from gdb_bmp.session import ...` whether this file is run as a
# standalone CLI or imported as `gdb_bmp.target_flash` from another script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gdb_bmp.session import (
    BmpError,
    BmpSession,
    BmpTargetConfig,
    add_target_arguments,
    check_responses,
    config_from_args,
    console_lines,
)


@dataclass
class BmpFlashConfig:
    """Configuration for flash-specific options.

    Attributes:
        flash_timeout_sec: Timeout for the load command.
        mass_erase: Run monitor erase_mass before load.
        verify: Run compare-sections after load.
    """

    flash_timeout_sec: float = 20.0
    mass_erase: bool = True
    verify: bool = True


# ── Exceptions ───────────────────────────────────────────────────────


class FlashError(BmpError):
    """Firmware load onto the target failed."""


class VerifyError(BmpError):
    """Post-flash verification (compare-sections) failed."""


# ── Public API ───────────────────────────────────────────────────────


def flash_target(
    target_config: BmpTargetConfig,
    flash_config: BmpFlashConfig | None = None,
) -> None:
    """Flash a target via Black Magic Probe.

    Executes the full pipeline:
      1. Open BmpSession (connect, scan, verify target, attach)
      2. Optionally mass erase target flash
      3. Load firmware
      4. Optionally verify (compare-sections)
      5. Kill (detach + reset)

    Args:
        target_config: BMP connection and target configuration.
        flash_config: Flash-specific options (uses defaults if *None*).

    Raises:
        FileNotFoundError: ELF file or GDB executable not found.
        BmpError: GDB / BMP communication error.
        FlashError: Firmware load failed.
        VerifyError: Post-flash verification failed.
    """
    if flash_config is None:
        flash_config = BmpFlashConfig()

    with BmpSession(target_config) as session:
        if flash_config.mass_erase:
            session.monitor("erase_mass", timeout_sec=30)

        responses = session.gdb.write(
            "load", timeout_sec=flash_config.flash_timeout_sec,
        )
        check_responses(responses, "load firmware", FlashError)

        if flash_config.verify:
            _compare_sections(session)


def _compare_sections(session: BmpSession) -> None:
    responses = session.gdb.write("compare-sections", timeout_sec=30)
    check_responses(responses, "compare-sections", VerifyError)

    for line in console_lines(responses):
        if "MIS-MATCHED" in line.upper():
            raise VerifyError(
                f"Flash verification failed: {line.strip()}"
            )


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash a target via Black Magic Probe.",
    )
    add_target_arguments(parser)

    parser.add_argument(
        "--no-mass-erase", action="store_true",
        help="Skip mass erase before flashing",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip post-flash verification (compare-sections)",
    )
    parser.add_argument(
        "--flash-timeout", type=float, default=60.0,
        help="Timeout for flash operation in seconds (default: 60)",
    )

    args = parser.parse_args()

    target_config = config_from_args(args)
    flash_config = BmpFlashConfig(
        flash_timeout_sec=args.flash_timeout,
        mass_erase=not args.no_mass_erase,
        verify=not args.no_verify,
    )

    try:
        flash_target(target_config, flash_config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except BmpError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Successfully flashed {args.elf_file.name} to '{args.target}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())

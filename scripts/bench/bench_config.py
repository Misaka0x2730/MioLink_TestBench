#!/usr/bin/env python3
"""Shared bench configuration model and firmware-variant derivation.

Parsing of the bench YAML and the rules that turn a parsed
:class:`BenchConfig` into the set of firmware artefacts the bench needs
live here so the build helpers (``build_miolink.py``,
``build_all_test_firmware.py``) and the test runner (``run_bench.py``)
agree on exactly one definition of:

  * the YAML schema (:class:`BenchConnection`, :class:`BenchConfig`,
    :func:`load_bench_config`);
  * which MioLink probe variants a connection exercises
    (:class:`BuildVariant`, :func:`variants_for`, :func:`resolve_uf2`);
  * where the per-(cli, swo) target firmware build lands
    (:func:`target_build_subdir`).
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# ── Constants ────────────────────────────────────────────────────────

_AUTO_BUILD_TARGET = "auto_rp2040"

# Boards covered by the multi-board auto-detect firmware. Probes whose
# `build_target` is in this set are also exercised with the auto build.
_AUTO_RP2040_BOARDS = frozenset({"pico", "pico_w", "miolink", "miolink_pico"})

# Build flavours always exercised per probe.
_BUILD_FLAVOURS = ("debug", "release")

# Fixed filename for staged MioLink probe images. The build helper
# (build_miolink.py) and the runner (run_bench.py) both resolve probe
# .uf2 paths through resolve_uf2(), so the name is defined once here and
# matches the artifact names produced by the MioLink build pipeline.
_PROBE_IMAGE_FILENAME = "MioLink-{board}-{flavour}.uf2"

# Allowed values for the YAML ``vtref`` field (target-power mode).
_VTREF_MODES = frozenset({"self-powered", "powered", "not_supported"})

# Allowed values for the YAML ``swo`` field (target SWO pin status).
_SWO_STATES = frozenset({"connected", "not_present"})

# Allowed values for the YAML ``cli`` list (CLI backend the test_board
# fixture firmware must be built for).
_CLI_MODES = frozenset({"uart", "rtt", "disabled"})

# Maps a YAML ``cli`` value to the ``CONFIG_CLI`` CMake value the
# test_board firmware expects (see firmware/CMakeLists.txt).
_CLI_TO_CMAKE = {"uart": "UART", "rtt": "RTT", "disabled": "NONE"}

# ── YAML model ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class BenchConnection:
    """One physical probe ↔ target connection on the bench."""

    probe: str
    serial: str
    build_target: str
    hardware_version: int | None
    part_number: str
    platform: str
    gdb_scan_name: str
    vtref: str
    swo: str
    cli: tuple[str, ...]
    interfaces: tuple[str, ...]
    uart: str
    reset_connected: bool
    vtref_voltage: float | None = None
    vtref_tolerance: float | None = None
    tpwr_settle_sec: float | None = None
    freq_hz: int | None = None
    enabled: bool = True


@dataclass(frozen=True)
class BenchConfig:
    """Top-level bench configuration as parsed from YAML."""

    connections: tuple[BenchConnection, ...] = field(default_factory=tuple)


def parse_frequency(value: str) -> int:
    """Parse a frequency string into Hz.

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


def load_bench_config(path: Path) -> BenchConfig:
    """Parse a bench YAML file into a :class:`BenchConfig`."""
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw_connections = raw.get("connections", [])
    if not isinstance(raw_connections, list) or not raw_connections:
        raise ValueError(
            f"{path}: 'connections' must be a non-empty list"
        )

    connections: list[BenchConnection] = []
    for idx, entry in enumerate(raw_connections):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{path}: connection #{idx} is not a mapping"
            )
        missing = [
            key for key in (
                "probe", "serial", "build_target", "part_number",
                "platform", "gdb_scan_name", "vtref", "swo", "cli",
                "interfaces",
            ) if key not in entry
        ]
        if missing:
            raise ValueError(
                f"{path}: connection #{idx} is missing required keys: "
                f"{', '.join(missing)}"
            )

        ifaces = entry["interfaces"]
        if not isinstance(ifaces, list) or not ifaces:
            raise ValueError(
                f"{path}: connection #{idx} 'interfaces' must be a "
                f"non-empty list"
            )

        vtref_mode = str(entry["vtref"])
        if vtref_mode not in _VTREF_MODES:
            raise ValueError(
                f"{path}: connection #{idx} 'vtref' must be one of "
                f"{sorted(_VTREF_MODES)} (got '{vtref_mode}')"
            )
        vtref_voltage: float | None = None
        vtref_tolerance: float | None = None
        tpwr_settle_sec: float | None = None
        if vtref_mode != "not_supported":
            for required_field in ("vtref_voltage", "vtref_tolerance"):
                if entry.get(required_field) is None:
                    raise ValueError(
                        f"{path}: connection #{idx} requires "
                        f"'{required_field}' when 'vtref' is "
                        f"'{vtref_mode}'"
                    )
            vtref_voltage = float(entry["vtref_voltage"])
            vtref_tolerance = float(entry["vtref_tolerance"])
        if vtref_mode == "powered":
            if entry.get("tpwr_settle_sec") is None:
                raise ValueError(
                    f"{path}: connection #{idx} requires "
                    f"'tpwr_settle_sec' when 'vtref' is 'powered'"
                )
            tpwr_settle_sec = float(entry["tpwr_settle_sec"])

        swo_state = str(entry["swo"])
        if swo_state not in _SWO_STATES:
            raise ValueError(
                f"{path}: connection #{idx} 'swo' must be one of "
                f"{sorted(_SWO_STATES)} (got '{swo_state}')"
            )

        cli_raw = entry["cli"]
        if not isinstance(cli_raw, list) or not cli_raw:
            raise ValueError(
                f"{path}: connection #{idx} 'cli' must be a non-empty list"
            )
        cli_modes = tuple(str(m) for m in cli_raw)
        bad_modes = [m for m in cli_modes if m not in _CLI_MODES]
        if bad_modes:
            raise ValueError(
                f"{path}: connection #{idx} 'cli' has invalid mode(s) "
                f"{bad_modes}; allowed: {sorted(_CLI_MODES)}"
            )
        if len(set(cli_modes)) != len(cli_modes):
            raise ValueError(
                f"{path}: connection #{idx} 'cli' has duplicate entries"
            )

        freq_raw = entry.get("freq")
        if freq_raw is None:
            freq_hz: int | None = None
        elif isinstance(freq_raw, str):
            try:
                freq_hz = parse_frequency(freq_raw)
            except argparse.ArgumentTypeError as exc:
                raise ValueError(
                    f"{path}: connection #{idx} 'freq' invalid: {exc}"
                ) from exc
        else:
            freq_hz = int(freq_raw)

        connections.append(BenchConnection(
            probe=str(entry["probe"]),
            serial=str(entry["serial"]),
            build_target=str(entry["build_target"]),
            hardware_version=(
                int(entry["hardware_version"])
                if "hardware_version" in entry
                and entry["hardware_version"] is not None
                else None
            ),
            part_number=str(entry["part_number"]),
            platform=str(entry["platform"]),
            gdb_scan_name=str(entry["gdb_scan_name"]),
            vtref=vtref_mode,
            swo=swo_state,
            cli=cli_modes,
            vtref_voltage=vtref_voltage,
            vtref_tolerance=vtref_tolerance,
            tpwr_settle_sec=tpwr_settle_sec,
            freq_hz=freq_hz,
            interfaces=tuple(str(i) for i in ifaces),
            uart=str(entry.get("uart", "main")),
            reset_connected=bool(entry.get("reset_connected", False)),
            enabled=bool(entry.get("enabled", True)),
        ))

    return BenchConfig(connections=tuple(connections))


# ── Variants ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BuildVariant:
    """A specific firmware build to exercise on a probe."""

    board: str       # PICO_BOARD value (e.g. "miolink", "auto_rp2040")
    flavour: str     # "debug" or "release"

    @property
    def label(self) -> str:
        return f"{self.board}/{self.flavour}"


def variants_for(connection: BenchConnection) -> list[BuildVariant]:
    """Enumerate firmware variants applicable to *connection*.

    Always exercises the probe's individual board build in both Debug
    and Release. Adds Debug + Release of the ``auto_rp2040`` multi-board
    build whenever the probe's ``build_target`` is covered by that
    firmware.
    """
    variants: list[BuildVariant] = []
    for flavour in _BUILD_FLAVOURS:
        variants.append(BuildVariant(connection.build_target, flavour))
    if connection.build_target in _AUTO_RP2040_BOARDS:
        for flavour in _BUILD_FLAVOURS:
            variants.append(BuildVariant(_AUTO_BUILD_TARGET, flavour))
    return variants


def resolve_uf2(
    probe_image_dir: Path,
    variant: BuildVariant,
) -> Path:
    """Resolve the staged ``.uf2`` path for *variant* under *probe_image_dir*.

    The filename is fixed (:data:`_PROBE_IMAGE_FILENAME`), so the build
    helper and the runner agree on it without a configurable pattern.
    """
    relative = _PROBE_IMAGE_FILENAME.format(
        board=variant.board, flavour=variant.flavour,
    )
    return (probe_image_dir / relative).resolve()


# ── Target firmware build layout ─────────────────────────────────────


def target_build_subdir(cli_mode: str, swo_state: str) -> str:
    """Return the per-(cli, swo) sub-directory name under target_build_dir.

    ``build_all_test_firmware.py`` configures one CMake build per
    ``(cli_mode, swo_state)`` pair into this sub-directory; ``run_bench.py``
    reads the matching ``test_board_<platform>.elf`` back from it.
    """
    return f"cli-{cli_mode}_swo-{swo_state}"

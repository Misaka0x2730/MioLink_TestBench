"""Frequency string parsing shared by the GDB CLI and the bench config.

Kept dependency-free (stdlib only) so config consumers such as
``bench_config`` can parse the YAML ``freq`` field without importing the
``gdb_bmp`` session module, which pulls in ``pygdbmi``. That dependency is
only needed at run time on the bench, not when building firmware from the
bench config.
"""

from __future__ import annotations

import argparse
import re


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

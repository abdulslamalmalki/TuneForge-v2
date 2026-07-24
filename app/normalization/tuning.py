"""Small, versioned vocabulary for the trusted-guidance pilot."""

from __future__ import annotations

TUNING_CONTRACT_VERSION = "1.0"

VALUE_KINDS = (
    "boolean",
    "enumeration",
    "integer",
    "decimal",
    "signed_offset",
    "range",
)
UNITS = (
    "unitless",
    "watt",
    "ampere",
    "milliwatt",
    "milliampere",
    "megahertz",
    "celsius",
    "multiplier",
)
SCOPES = (
    "global",
    "all_core",
    "per_core",
    "ccd",
    "gfx",
)
GOALS = (
    "daily_stability",
    "performance",
    "efficiency",
    "lower_temperature",
    "lower_noise",
    "benchmarking",
)


def tuning_contract() -> dict[str, object]:
    return {
        "version": TUNING_CONTRACT_VERSION,
        "value_kinds": list(VALUE_KINDS),
        "units": list(UNITS),
        "scopes": list(SCOPES),
        "goals": list(GOALS),
    }

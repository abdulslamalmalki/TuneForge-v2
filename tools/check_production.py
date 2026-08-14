"""Validate TuneForge configuration and durable storage before migration/startup."""

from __future__ import annotations

import json

from app.operations import preflight


def main() -> int:
    result = preflight.check()
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

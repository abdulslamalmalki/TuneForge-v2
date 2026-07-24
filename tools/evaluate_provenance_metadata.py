"""Dry-run cover-page provenance metadata extraction without changing the database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.provenance.metadata import extract_metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="Optional JSON report destination")
    args = parser.parse_args()
    rows = []
    for path in args.pdfs:
        print(f"Analyzing {path}", flush=True)
        metadata = extract_metadata(path, path.name)
        rows.append({"path": str(path), **metadata.as_dict()})
    rendered = json.dumps({"manuals": rows}, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()

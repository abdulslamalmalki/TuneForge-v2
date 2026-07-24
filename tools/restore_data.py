"""Verify and restore a TuneForge archive to a new directory."""

import argparse
from pathlib import Path

from app.operations.backup import BackupError, restore_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--restore-to", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = restore_backup(args.archive, args.restore_to)
    except BackupError as error:
        parser.error(str(error))
    print(f"Restored to: {result.target_dir}")
    print(f"Database revision: {result.database_revision}")
    print(f"Files: {result.file_count}")
    print("Authentication sessions: revoked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

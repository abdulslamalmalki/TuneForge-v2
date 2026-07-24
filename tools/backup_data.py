"""Create a verified TuneForge database-and-source archive."""

import argparse
from pathlib import Path

from app import config
from app.operations.backup import BackupError, create_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    args = parser.parse_args()
    try:
        result = create_backup(
            args.data_dir,
            args.output,
            progress=lambda message: print(message, flush=True),
        )
    except BackupError as error:
        parser.error(str(error))
    print(f"Archive: {result.archive_path}")
    print(f"SHA-256: {result.archive_sha256}")
    print(f"Database revision: {result.database_revision}")
    print(f"Files: {result.file_count}")
    print(f"Uncompressed bytes: {result.uncompressed_bytes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

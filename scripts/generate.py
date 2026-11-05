"""Synchronize or check generated schema, catalog, rule pages and skill references."""

import argparse
import sys
from pathlib import Path

from smelt.docs import check_generated_files, write_generated_files

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report drift without writing files"
    )
    args = parser.parse_args()
    if args.check:
        drift = check_generated_files(ROOT)
        for name in drift:
            sys.stdout.write(f"out of date: {name}\n")
        if drift:
            sys.stdout.write("run `uv run python scripts/generate.py` to synchronize\n")
        else:
            sys.stdout.write("everything up to date\n")
        return 1 if drift else 0
    changed = write_generated_files(ROOT)
    for name in changed:
        sys.stdout.write(f"changed {name}\n")
    if not changed:
        sys.stdout.write("everything up to date\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

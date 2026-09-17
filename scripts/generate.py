"""Regenerate smelt.schema.json and docs/rules/ from the code."""

import sys
from pathlib import Path

from smelt.docs import write_generated_files

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    changed = write_generated_files(ROOT)
    for name in changed:
        sys.stdout.write(f"wrote {name}\n")
    if not changed:
        sys.stdout.write("everything up to date\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Install native distributions in clean environments outside the checkout."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], root: Path) -> str:
    completed = subprocess.run(  # noqa: S603 - explicit argv, never a shell
        command,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=240,
    )
    if completed.returncode:
        raise RuntimeError(f"{command[0]} failed:\n{completed.stderr}")
    return completed.stdout


def smoke(directory: Path) -> None:
    distributions = [*directory.glob("*.whl"), *directory.glob("*.tar.gz")]
    if not distributions:
        raise ValueError("no distributions to smoke-test")
    for distribution in distributions:
        with tempfile.TemporaryDirectory(prefix="smelt-wheel-") as temporary:
            root = Path(temporary).resolve()
            environment = root / "environment"
            run(["uv", "venv", "--python", sys.executable, str(environment)], root)
            scripts = environment / ("Scripts" if os.name == "nt" else "bin")
            python = scripts / ("python.exe" if os.name == "nt" else "python")
            binary = scripts / ("smelt.exe" if os.name == "nt" else "smelt")
            run(
                ["uv", "pip", "install", "--python", str(python), str(distribution)],
                root,
            )
            package = Path(
                run(
                    [str(python), "-c", "import smelt; print(smelt.__file__)"], root
                ).strip()
            )
            if not package.resolve().is_relative_to(environment.resolve()):
                raise AssertionError(
                    f"smoke test imported outside its wheel: {package}"
                )
            catalog = package.parent / "rules/catalog.json"
            if not json.loads(catalog.read_text(encoding="utf-8")):
                raise AssertionError("missing packaged rule catalog")
            for arguments in (
                ["--version"],
                ["rules"],
                ["explain", "SMT101"],
                ["config", "schema"],
            ):
                run([str(binary), *arguments], root)
            print(f"Installed and checked {distribution.name}")  # noqa: T201


if __name__ == "__main__":
    smoke(Path(sys.argv[1]).resolve())

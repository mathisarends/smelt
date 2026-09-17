import sys
import textwrap
from typing import TYPE_CHECKING

import pytest

from smelt.analysis.context import AnalysisContext
from smelt.analysis.types import PROBE_MODULE, PyrightTypes
from smelt.config import load_config
from tests.helpers import write_project

if TYPE_CHECKING:
    from pathlib import Path

PLAIN_CONFIG = """
version: 1
project:
  root_packages: [gw]
"""

# A stand-in for pyright: it reports an error on every probe line that mentions
# a name from failing.txt, and appends one line to runs.log per run.
FAKE_PYRIGHT = """
import json
import sys
from pathlib import Path

here = Path(__file__).parent
workspace = Path(sys.argv[-1])
probe = (workspace / "{probe}.py").read_text(encoding="utf-8")
failing = here.joinpath("failing.txt").read_text(encoding="utf-8").split()
with here.joinpath("runs.log").open("a", encoding="utf-8") as log:
    log.write("run\\n")

diagnostics = [
    {{
        "severity": "error",
        "message": "type mismatch",
        "range": {{"start": {{"line": number, "character": 0}}}},
    }}
    for number, line in enumerate(probe.splitlines())
    if any(name in line for name in failing)
]
print(json.dumps({{"generalDiagnostics": diagnostics}}))
"""

SESSIONS = ("gw.infra.sql.SqlSessions", "gw.app.ports.Sessions")
CALLS = ("gw.infra.sql.SqlCalls", "gw.app.ports.Calls")


@pytest.fixture
def pyright(tmp_path: Path) -> Path:
    """A fake pyright that rejects anything named SqlCalls."""
    script = tmp_path / "fake_pyright.py"
    script.write_text(
        textwrap.dedent(FAKE_PYRIGHT.format(probe=PROBE_MODULE)).lstrip("\n"),
        encoding="utf-8",
    )
    (tmp_path / "failing.txt").write_text("SqlCalls\n", encoding="utf-8")
    return script


def _types(root: Path, script: Path) -> PyrightTypes:
    return PyrightTypes(
        root=root, source_roots=(".",), command=(sys.executable, str(script))
    )


def _runs(script: Path) -> int:
    log = script.with_name("runs.log")
    return len(log.read_text(encoding="utf-8").splitlines()) if log.is_file() else 0


class TestImplements:
    def test_answers_come_from_pyright(self, tmp_path: Path, pyright: Path) -> None:
        types = _types(tmp_path, pyright)

        assert types.implements(*SESSIONS)
        assert not types.implements(*CALLS)

    def test_a_batch_is_a_single_run(self, tmp_path: Path, pyright: Path) -> None:
        types = _types(tmp_path, pyright)

        types.prepare([SESSIONS, CALLS])
        types.implements(*SESSIONS)
        types.implements(*CALLS)

        assert _runs(pyright) == 1

    def test_a_name_without_a_module_is_never_implemented(
        self, tmp_path: Path, pyright: Path
    ) -> None:
        types = _types(tmp_path, pyright)

        assert not types.implements("Sessions", "gw.app.ports.Sessions")

    def test_a_backend_that_cannot_run_answers_no(self, tmp_path: Path) -> None:
        types = PyrightTypes(
            root=tmp_path, source_roots=(".",), command=("definitely-not-a-binary",)
        )

        assert not types.implements(*SESSIONS)

    def test_resolve_type_is_not_available(self, tmp_path: Path) -> None:
        types = PyrightTypes(root=tmp_path, source_roots=(".",), command=("pyright",))

        assert types.resolve_type("gw/app/ports.py", 1, 1) is None


class TestProbeWorkspace:
    def test_pyright_is_pointed_at_the_source_roots(self, tmp_path: Path) -> None:
        types = PyrightTypes(
            root=tmp_path, source_roots=("src", "."), command=("pyright",)
        )

        config = types.pyright_config()

        assert config["include"] == [f"{PROBE_MODULE}.py"]
        assert config["extraPaths"] == [
            str((tmp_path / "src").resolve()),
            str(tmp_path.resolve()),
        ]


class TestDiscovery:
    def _config(self, tmp_path: Path, command: str | None = None) -> Path:
        text = PLAIN_CONFIG
        if command is not None:
            text += f'analysis:\n  types: pyright\n  pyright_command: ["{command}"]\n'
        return write_project(tmp_path, {"smelt.yaml": text, "gw/__init__.py": ""})

    def test_no_backend_by_default(self, tmp_path: Path) -> None:
        root = self._config(tmp_path)
        loaded = load_config(root / "smelt.yaml")

        assert loaded.config.analysis.types == "none"
        assert AnalysisContext(root, loaded.config).types is None

    def test_pyright_backend_is_built_when_configured(self, tmp_path: Path) -> None:
        root = self._config(tmp_path, command=sys.executable.replace("\\", "/"))
        loaded = load_config(root / "smelt.yaml")

        types = AnalysisContext(root, loaded.config).types

        assert isinstance(types, PyrightTypes)
        assert types.source_roots == (".",)

    def test_a_missing_executable_leaves_the_backend_out(self, tmp_path: Path) -> None:
        root = self._config(tmp_path, command="definitely-not-a-binary")
        loaded = load_config(root / "smelt.yaml")

        assert AnalysisContext(root, loaded.config).types is None

    def test_an_explicit_backend_wins(self, tmp_path: Path, pyright: Path) -> None:
        root = self._config(tmp_path)
        loaded = load_config(root / "smelt.yaml")
        given = _types(root, pyright)

        assert AnalysisContext(root, loaded.config, types=given).types is given

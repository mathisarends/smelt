from __future__ import annotations

from typing import TYPE_CHECKING

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

POLICY = """\
version: 1
project: {root_packages: [app]}
structure:
  contracts:
    models:
      match: app
      bases: {pydantic.BaseModel: app.core.Options}
"""
SELECT = CheckOptions(select=("SMT353",))
OPTIONS = (
    "from pydantic import BaseModel, ConfigDict\n\n\n"
    "class Options(BaseModel):\n"
    '    model_config = ConfigDict(extra="forbid", frozen=True)\n'
)


def _project(
    tmp_path: Path, source: str, *, extra: dict[str, str] | None = None
) -> Path:
    return write_project(
        tmp_path,
        {
            "smelt.yml": POLICY,
            "app/__init__.py": "",
            "app/core/__init__.py": "from app.core.options import Options\n",
            "app/core/options.py": OPTIONS,
            "app/manifest.py": source,
            **(extra or {}),
        },
    )


def _classes(root: Path) -> list[tuple[str | None, str]]:
    return [
        (item.path, item.expected["class"])
        for item in violations(root, SELECT)
        if item.expected is not None
    ]


def test_reports_direct_outside_base_from_the_issue(tmp_path: Path) -> None:
    source = (
        "from pydantic import BaseModel, ConfigDict\n\n\n"
        "class _Manifest(BaseModel):\n"
        '    model_config = ConfigDict(extra="forbid")\n'
    )

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT353",
        "app/manifest.py",
        4,
        1,
    )
    assert finding.message == (
        "Class _Manifest inherits pydantic.BaseModel directly; "
        "models expects app.core.Options"
    )
    assert finding.expected == {
        "contract": "models",
        "class": "_Manifest",
        "outside_base": "pydantic.BaseModel",
        "project_base": "app.core.Options",
        "automatic_rewrite": False,
    }
    assert (
        finding.hint == "Inherit Options from app.core instead of pydantic.BaseModel."
    )


def test_resolves_aliases_module_attributes_and_generic_bases(
    tmp_path: Path,
) -> None:
    source = (
        "from typing import Generic, TypeVar\n\n"
        "import pydantic\n"
        "import pydantic as pd\n"
        "from pydantic import BaseModel as Model\n\n"
        "T = TypeVar('T')\n\n\n"
        "class Aliased(Model):\n    name: str\n\n\n"
        "class Attribute(pydantic.BaseModel):\n    name: str\n\n\n"
        "class Renamed(pd.BaseModel):\n    name: str\n\n\n"
        "class Page(Model, Generic[T]):\n    items: list[T]\n"
    )

    assert _classes(_project(tmp_path, source)) == [
        ("app/manifest.py", "Aliased"),
        ("app/manifest.py", "Attribute"),
        ("app/manifest.py", "Renamed"),
        ("app/manifest.py", "Page"),
    ]


def test_accepts_project_base_and_other_uses_of_the_import(
    tmp_path: Path,
) -> None:
    source = (
        "from pydantic import BaseModel, TypeAdapter\n\n"
        "from app.core import Options\n\n\n"
        "class Manifest(Options):\n    name: str\n\n\n"
        "def is_model(value: object) -> bool:\n"
        "    return isinstance(value, BaseModel)\n\n\n"
        "def load(raw: bytes) -> BaseModel:\n"
        "    return TypeAdapter(Manifest).validate_json(raw)\n"
    )

    assert _classes(_project(tmp_path, source)) == []


def test_exempts_the_module_defining_the_project_base(tmp_path: Path) -> None:
    root = _project(tmp_path, "")

    assert _classes(root) == []


def test_reports_outside_base_in_other_modules_of_the_base_package(
    tmp_path: Path,
) -> None:
    source = "from pydantic import BaseModel\n\n\nclass Settings(BaseModel):\n    name: str\n"
    root = _project(tmp_path, "", extra={"app/core/settings.py": source})

    assert _classes(root) == [("app/core/settings.py", "Settings")]


def test_nested_package_can_lift_the_mapping(tmp_path: Path) -> None:
    config = POLICY + ("    wire:\n      match: app.wire\n      bases: {}\n")
    source = (
        "from pydantic import BaseModel\n\n\nclass Payload(BaseModel):\n    name: str\n"
    )
    root = write_project(
        tmp_path,
        {
            "smelt.yml": config,
            "app/__init__.py": "",
            "app/wire/__init__.py": "",
            "app/wire/payloads.py": source,
            "app/events.py": source,
        },
    )

    assert _classes(root) == [("app/events.py", "Payload")]


def test_accepts_a_reasoned_suppression(tmp_path: Path) -> None:
    source = (
        "from pydantic import BaseModel\n\n\n"
        "class Raw(BaseModel):  # smelt: ignore[SMT353] -- mirrors a vendor schema\n"
        "    name: str\n"
    )

    assert _classes(_project(tmp_path, source)) == []


def test_ignores_tests(tmp_path: Path) -> None:
    source = (
        "from pydantic import BaseModel\n\n\nclass Case(BaseModel):\n    name: str\n"
    )
    root = _project(tmp_path, "", extra={"tests/test_models.py": source})

    assert _classes(root) == []


def test_is_inactive_without_policy(tmp_path: Path) -> None:
    root = write_project(
        tmp_path,
        {
            "smelt.yml": "version: 1\nproject: {root_packages: [app]}\n",
            "app/__init__.py": "",
            "app/models.py": "from pydantic import BaseModel\n\n\n"
            "class Manifest(BaseModel):\n    name: str\n",
        },
    )

    assert [item for item in violations(root) if item.code == "SMT353"] == []

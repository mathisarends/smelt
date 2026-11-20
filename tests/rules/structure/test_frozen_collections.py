from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT360",))


@pytest.mark.parametrize(
    ("imports", "decorator", "annotation", "kind"),
    [
        ("from dataclasses import dataclass", "dataclass", "dict[str, str]", "dict"),
        ("import dataclasses as dc", "dc.dataclass", "list[int]", "list"),
        (
            "from dataclasses import dataclass as dc\nfrom builtins import set as Tags",
            "dc",
            "Tags[str]",
            "set",
        ),
        (
            "import dataclasses\nimport builtins as b",
            "dataclasses.dataclass",
            '"b.dict[str, str]"',
            "dict",
        ),
        ("from dataclasses import dataclass", "dataclass", "list", "list"),
    ],
)
def test_reports_mutable_instance_field_with_resolved_type(
    tmp_path: Path, imports: str, *, decorator: str, annotation: str, kind: str
) -> None:
    root = _project(
        tmp_path,
        f"{imports}\n@{decorator}(frozen=True)\nclass Snapshot:\n    values: {annotation}\n",
    )

    (finding,) = violations(root, SELECT)

    assert (finding.code, finding.path, finding.column) == ("SMT360", "app/model.py", 5)
    assert finding.expected == {
        "class": "Snapshot",
        "field": "values",
        "mutable_type": f"builtins.{kind}",
        "automatic_rewrite": False,
    }
    assert finding.line == len(imports.splitlines()) + 3


def test_reports_each_mutable_field_not_classvars_or_initvars(tmp_path: Path) -> None:
    root = _project(
        tmp_path,
        """
        from dataclasses import dataclass, InitVar
        from typing import ClassVar as Shared
        from collections.abc import Mapping

        @dataclass(frozen=True)
        class Snapshot:
            names: dict[str, str]
            items: list[int]
            tags: set[str]
            shared: Shared[list[int]]
            initial: InitVar[list[int]]
            stable: tuple[str, ...]
            frozen: frozenset[str]
            view: Mapping[str, str]
            unknown: object
            nested: tuple[list[int], ...]
            optional: list[int] | None
    """,
    )

    assert [
        item.expected["field"] for item in violations(root, SELECT) if item.expected
    ] == ["names", "items", "tags"]


@pytest.mark.parametrize(
    "source",
    [
        "from dataclasses import dataclass\n@dataclass\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\n@dataclass(frozen=False)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\n@dataclass(frozen=FLAG)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\n@dataclass(frozen=True, **opts)\nclass C:\n    x: list[int]",
        "class C:\n    x: list[int]",
        "from custom import dataclass\n@dataclass(frozen=True)\nclass C:\n    x: list[int]",
        "from . import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\ndataclass = custom\n@dataclass(frozen=True)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\nclass list: pass\n@dataclass(frozen=True)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\nfrom custom import *\n@dataclass(frozen=True)\nclass C:\n    x: list[int]",
        "from dataclasses import dataclass\ntype Items = list[int]\n@dataclass(frozen=True)\nclass C:\n    x: Items",
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass C[list]:\n    x: list[int]",
        "def factory():\n    from dataclasses import dataclass\n    @dataclass(frozen=True)\n    class C:\n        x: list[int]",
    ],
)
def test_skips_unfrozen_or_uncertain_declarations(tmp_path: Path, source: str) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


def test_is_opt_in_and_supports_intentional_reference_holder(tmp_path: Path) -> None:
    source = """
        from dataclasses import dataclass
        @dataclass(frozen=True)
        class Reference:
            values: list[int]  # smelt: ignore[SMT360] -- intentionally shares an append-only log
    """
    root = _project(tmp_path, source)
    assert not any(item.code == "SMT360" for item in violations(root))
    assert violations(root, SELECT) == []

    (root / "smelt.yml").write_text(
        CONFIG + "rules: {modeling/mutable-field-in-frozen-dataclass: warning}\n",
        encoding="utf-8",
    )
    assert violations(root) == []
    write_project(root, {"app/model.py": source.replace("# smelt:", "# reason:")})
    (finding,) = violations(root)
    assert finding.code == "SMT360"
    assert violations(root, CheckOptions(use_cache=False)) == violations(root)


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/model.py": source},
    )

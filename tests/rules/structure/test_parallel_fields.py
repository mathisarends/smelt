from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 2\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("MOD012",))


def test_groups_zip_sites_and_explains_field_declarations(tmp_path: Path) -> None:
    source = (
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True)\n"
        "class Layout:\n"
        "    models: tuple[type, ...] = ()\n"
        "    fields: tuple[tuple[str, ...], ...] = ()\n"
        "    def split(self):\n"
        "        for model, names in zip(self.models, self.fields, strict=True):\n"
        "            yield model, names\n"
        "    def reverse(self):\n"
        "        return zip(self.fields, self.models)\n"
    )

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "MOD012",
        "app/model.py",
        7,
        29,
    )
    assert finding.severity == Severity.HINT
    assert finding.expected == {
        "class": "Layout",
        "fields": ["fields", "models"],
        "pairing_edges": ["Layout: fields, models"],
        "declarations": [
            {"field": "fields", "line": 5, "column": 5},
            {"field": "models", "line": 4, "column": 5},
        ],
        "zip_sites": [{"line": 7, "column": 29}, {"line": 10, "column": 16}],
        "automatic_rewrite": False,
    }


def test_resolves_import_aliases_and_preceding_field_alias(tmp_path: Path) -> None:
    source = """
        from builtins import zip as pair
        from collections.abc import Sequence as Items
        import typing as t
        class Layout:
            models: "Items[type]"
            fields: t.Tuple[t.Tuple[str, ...], ...]
            async def split(this):
                models = this.models
                fields: object = this.fields
                return [model for model, names in pair(models, fields, strict=True)]
    """

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert finding.expected is not None
    assert finding.expected["fields"] == ["fields", "models"]


@pytest.mark.parametrize(
    "method",
    [
        "def run(self, messages):\n    return zip(messages, self.fields)",
        "def run(self, other):\n    return zip(self.models, other.fields)",
        "def run(self):\n    return zip(self.models, self.models)",
        "def run(self):\n    return zip(self.models, *self.fields)",
        "def run(self):\n    models = self.models\n    models = []\n    return zip(models, self.fields)",
        "def run(self):\n    if ready:\n        models = self.models\n    return zip(models, self.fields)",
        "def run(self):\n    def nested():\n        return zip(self.models, self.fields)\n    return nested",
        "@staticmethod\ndef run(self):\n    return zip(self.models, self.fields)",
        "def run(self, other):\n    self = other\n    return zip(self.models, self.fields)",
    ],
)
def test_skips_local_different_or_uncertain_receivers(
    tmp_path: Path, method: str
) -> None:
    source = (
        "class Layout:\n    models: list[type]\n    fields: list[str]\n"
        + "\n".join("    " + line for line in method.splitlines())
        + "\n"
    )
    assert violations(_project(tmp_path, source), SELECT) == []


@pytest.mark.parametrize(
    "declarations",
    [
        "models: ClassVar[list[type]]\nfields: list[str]",
        "models: object\nfields: list[str]",
        "models = []\nfields = []",
        "models: list[type]\nfields: list[str]\n@property\ndef fields(self): return []",
        "models: list[type]\nfields: list[str]\nfields = external",
    ],
)
def test_skips_non_instance_or_unresolved_fields(
    tmp_path: Path, declarations: str
) -> None:
    source = (
        "from typing import ClassVar\nclass Layout:\n"
        + "\n".join("    " + line for line in declarations.splitlines())
        + "\n    def run(self):\n        return zip(self.models, self.fields)\n"
    )
    assert violations(_project(tmp_path, source), SELECT) == []


def test_skips_shadowed_zip_and_inherited_fields(tmp_path: Path) -> None:
    source = """
        from custom import zip
        class Layout:
            models: list[type]
            fields: list[str]
            def run(self):
                return zip(self.models, self.fields)
        class Child(Layout):
            def pairs(self):
                return zip(self.models, self.fields)
    """
    root = _project(tmp_path, source)
    assert violations(root, SELECT) == []
    write_project(root, {"app/model.py": source.replace("from custom import zip", "")})
    (finding,) = violations(root, SELECT)
    assert finding.expected is not None
    assert finding.expected["class"] == "Layout"


def test_opt_in_and_intentional_column_storage_suppression(tmp_path: Path) -> None:
    source = """
        class Columns:
            x: list[float]
            y: list[float]
            def points(self):
                return zip(self.x, self.y)  # smelt: ignore[MOD012] -- deliberate numerical column storage
    """
    root = _project(tmp_path, source)
    assert not any(item.code == "MOD012" for item in violations(root))
    assert violations(root, SELECT) == []
    (root / "smelt.yml").write_text(
        CONFIG + "rules: {modeling/parallel-record-fields: hint}\n", encoding="utf-8"
    )
    assert violations(root) == []
    write_project(root, {"app/model.py": source.replace("# smelt:", "# reason:")})
    (finding,) = violations(root)
    assert finding.code == "MOD012"


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": CONFIG, "app/__init__.py": "", "app/model.py": source},
    )


def test_old_field_group_cannot_hide_a_replacement_behind_same_zip_aliases(
    tmp_path: Path,
) -> None:
    source = """
        class Layout:
            models: list[type]
            fields: list[str]
            alternate: list[type]
            def pairs(self):
                models = self.models
                return zip(models, self.fields)
    """
    root = _project(tmp_path, source)
    original = violations(root, SELECT)
    debt = Debt.from_violations(original, lambda _: "return zip(models, self.fields)")
    write_project(
        root,
        {
            "app/model.py": source.replace(
                "models = self.models", "models = self.alternate"
            )
        },
    )
    current = violations(root, SELECT)

    new, known, resolved = debt.match(
        current, lambda _: "return zip(models, self.fields)"
    )

    assert [item.code for item in new] == ["MOD012"]
    assert known == []
    assert len(resolved) == 1

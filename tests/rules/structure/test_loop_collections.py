from __future__ import annotations

from textwrap import dedent
from typing import TYPE_CHECKING

import pytest

from smelt.diagnostics.debt import Debt
from smelt.diagnostics.violation import Severity
from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
SELECT = CheckOptions(select=("SMT362",))
SNAPSHOT = dedent("""
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Turn:
        id: str

    @dataclass(frozen=True)
    class Step:
        turn_id: str
        start: int
        boundary: int

    @dataclass(frozen=True)
    class Session:
        turns: tuple[Turn, ...]
        steps: tuple[Step, ...]

        def __post_init__(self):
            previous = 0
            for step in self.steps:
                if not previous <= step.start < step.boundary:
                    raise ValueError("Invalid order")
                if step.turn_id not in {turn.id for turn in self.turns}:
                    raise ValueError("Unknown turn")
                previous = step.boundary
""").lstrip("\n")


def test_reports_reduced_snapshot_membership_with_explicit_evidence(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, SNAPSHOT)

    (finding,) = violations(root, SELECT)

    assert (finding.code, finding.path, finding.line, finding.column) == (
        "SMT362",
        "app/state.py",
        23,
        36,
    )
    assert finding.severity == Severity.HINT
    assert finding.expected is not None
    assert finding.expected["loop_line"] == 20
    assert finding.expected["function"] == "__post_init__"
    assert finding.expected["expression"] == "{turn.id for turn in self.turns}"
    assert finding.expected["automatic_rewrite"] is False


def test_groups_equivalent_projections_with_renamed_generator_variables(
    tmp_path: Path,
) -> None:
    source = """
        def validate(wanted: tuple[str, ...], available: tuple[str, ...]):
            for name in wanted:
                if name not in {item for item in available}:
                    raise ValueError("Unknown")
                present = name in {other for other in available}
    """

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert finding.expected is not None
    assert finding.expected["sites"] == [
        {"line": 3, "column": 24},
        {"line": 5, "column": 27},
    ]
    assert len(finding.expected["lookup_edges"]) == 1


def test_accepts_literal_tuple_evidence_and_pure_index_projection(
    tmp_path: Path,
) -> None:
    source = """
        def validate():
            available = ((1, 10), (2, 20))
            wanted = (1, 2)
            for value in wanted:
                if value not in {pair[0] for pair in available}:
                    return False
            return True
    """

    (finding,) = violations(_project(tmp_path, source), SELECT)

    assert finding.expected is not None
    assert finding.expected["expression"] == "{pair[0] for pair in available}"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("tuple[Turn, ...]", "list[Turn]"),
        ("tuple[Step, ...]", "list[Step]"),
        ("@dataclass(frozen=True)", "@dataclass"),
        ("id: str", "id: object"),
        ("class Turn:", "class Turn(External):"),
        (
            "from dataclasses import dataclass",
            "from pydantic.dataclasses import dataclass",
        ),
        ("id: str", "id: Annotated[str, Validator]"),
        ("{turn.id for turn in self.turns}", "{callback(turn) for turn in self.turns}"),
        ("{turn.id for turn in self.turns}", "{turn.id for turn in step.turns}"),
        (
            "{turn.id for turn in self.turns}",
            "{turn.id for turn in self.turns if turn.id}",
        ),
        (
            "{turn.id for turn in self.turns}",
            "{turn.id for group in self.turns for turn in group}",
        ),
        ("previous = step.boundary", "callback()"),
        (
            "previous = step.boundary",
            "alias = self.turns\n            alias.append(step)",
        ),
        ("previous = step.boundary", "self.turns = ()"),
        ("previous = step.boundary", "object.__setattr__(self, 'turns', ())"),
        ("previous = step.boundary", "previous = external.property"),
        ("previous = step.boundary", "yield step"),
        ("def __post_init__(self)", "async def __post_init__(self)"),
    ],
)
def test_stays_quiet_when_stability_or_effects_are_uncertain(
    tmp_path: Path, old: str, new: str
) -> None:
    source = SNAPSHOT.replace(old, new, 1)
    if new.startswith("async def"):
        source = source.replace("previous = step.boundary", "await ready()")
    assert violations(_project(tmp_path, source), SELECT) == []


@pytest.mark.parametrize(
    "body",
    [
        "available = (name,)\nif name not in {item for item in available}:\n    pass",
        "if name not in {item for item in available}:\n    pass\navailable = (name,)",
        "alias = available\nalias[0] = name\nif name not in {item for item in available}:\n    pass",
        "if name not in {item for item in available}:\n    pass\ncallback()",
        "if name not in {item for item in available}:\n    pass\nfor inner in available:\n    pass",
    ],
)
def test_skips_loop_dependent_rebound_or_mutated_inputs(
    tmp_path: Path, body: str
) -> None:
    source = (
        "def validate(wanted: tuple[str, ...], available: tuple[str, ...]):\n"
        "    for name in wanted:\n"
        + "\n".join("        " + line for line in body.splitlines())
        + "\n"
    )
    assert violations(_project(tmp_path, source), SELECT) == []


@pytest.mark.parametrize(
    "field",
    [
        "id: str\n    @property\n    def id(self): return source.next()",
        "id: str\n    id = descriptor()",
        "id: str\n    def __getattribute__(self, name): return source.next()",
    ],
)
def test_skips_properties_descriptors_and_custom_attribute_access(
    tmp_path: Path, field: str
) -> None:
    source = SNAPSHOT.replace("id: str", field, 1)
    assert violations(_project(tmp_path, source), SELECT) == []


def test_supports_import_and_quoted_type_aliases(tmp_path: Path) -> None:
    source = SNAPSHOT.replace(
        "from dataclasses import dataclass",
        "import dataclasses as dc\nfrom builtins import str as Text\nfrom typing import Tuple as Items",
    )
    source = source.replace("@dataclass", "@dc.dataclass").replace(
        "id: str", "id: Text"
    )
    source = source.replace("tuple[Turn, ...]", '"Items[Turn, ...]"')
    (finding,) = violations(_project(tmp_path, source), SELECT)
    assert finding.code == "SMT362"


def test_is_opt_in_and_supports_package_overrides_and_suppression(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path, SNAPSHOT)
    assert not any(item.code == "SMT362" for item in violations(root))
    (root / "smelt.yml").write_text(
        CONFIG
        + "rules: {idioms/loop-invariant-collection: hint}\n"
        + "style:\n  overrides:\n    quiet:\n      match: app.quiet\n"
        + "      rules: {idioms/loop-invariant-collection: off}\n",
        encoding="utf-8",
    )
    write_project(root, {"app/quiet/__init__.py": SNAPSHOT})
    (finding,) = violations(root)
    assert finding.path == "app/state.py"
    write_project(
        root,
        {
            "app/state.py": SNAPSHOT.replace(
                "if step.turn_id not in {turn.id for turn in self.turns}:",
                "if step.turn_id not in {turn.id for turn in self.turns}:  # smelt: ignore[SMT362] -- preserve evaluation at this guard",
            )
        },
    )
    assert violations(root) == []


def test_cached_results_follow_field_declaration_changes(tmp_path: Path) -> None:
    root = _project(tmp_path, SNAPSHOT)
    (finding,) = violations(root, SELECT)
    assert finding.code == "SMT362"
    write_project(root, {"app/state.py": SNAPSHOT.replace("id: str", "id: object")})
    assert (
        violations(root, SELECT)
        == violations(root, CheckOptions(select=("SMT362",), use_cache=False))
        == []
    )


@pytest.mark.parametrize(
    "source",
    [
        "def check(items: tuple[str, ...]):\n    while ready:\n        if name in {item for item in items}:\n            pass\n",
        "def check(items: tuple[str, ...]):\n    for name in items:\n        copy = {item for item in items}\n",
        "from external import Turn\ndef check(items: tuple[Turn, ...], names: tuple[str, ...]):\n    for name in names:\n        if name in {item.id for item in items}:\n            pass\n",
        "type Names = tuple[str, ...]\ndef check(items: Names, names: tuple[str, ...]):\n    for name in names:\n        if name in {item for item in items}:\n            pass\n",
        "def check(items: tuple[str, ...], groups: tuple[tuple[str, ...], ...]):\n    for group in groups:\n        if 'name' in {item for item in group}:\n            pass\n",
    ],
)
def test_keeps_unsupported_or_non_membership_patterns_quiet(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), SELECT) == []


def test_multiline_projection_replacement_is_not_hidden_by_old_debt(
    tmp_path: Path,
) -> None:
    source = SNAPSHOT.replace("id: str", "id: str\n    external_id: str", 1)
    source = source.replace(
        "{turn.id for turn in self.turns}",
        "{\n                    turn.id for turn in self.turns\n                }",
    )
    root = _project(tmp_path, source)
    baseline = Debt.from_violations(
        violations(root, SELECT), lambda _: "if step.turn_id not in {"
    )
    write_project(
        root,
        {
            "app/state.py": source.replace(
                "turn.id for turn", "turn.external_id for turn"
            )
        },
    )
    new, known, resolved = baseline.match(
        violations(root, SELECT), lambda _: "if step.turn_id not in {"
    )
    assert [item.code for item in new] == ["SMT362"]
    assert known == []
    assert len(resolved) == 1


def _project(tmp_path: Path, source: str) -> Path:
    return write_project(
        tmp_path, {"smelt.yml": CONFIG, "app/__init__.py": "", "app/state.py": source}
    )

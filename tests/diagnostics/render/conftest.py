import pytest

from smelt.diagnostics.report import Report, RuleMeta
from smelt.diagnostics.violation import (
    Category,
    ImportLink,
    Severity,
    Violation,
)

SOURCES = {
    "gw/features/voice/domain/calls.py": "from gw.features.voice.infra import sql\n",
    "gw/features/voice/infra/sql.py": "class SqlSessions:\n",
}

LAYER_VIOLATION = Violation(
    code="SMT101",
    rule="layer-boundary",
    severity=Severity.ERROR,
    message="domain must not import infrastructure",
    path="gw/features/voice/domain/calls.py",
    line=1,
    column=1,
    end_line=1,
    end_column=40,
    source_module="gw.features.voice.domain.calls",
    target_module="gw.features.voice.infra.sql",
    import_chain=(
        ImportLink("gw.features.voice.domain.calls", "gw.features.voice.infra.sql", 1),
    ),
    feature="voice",
    layer="domain",
    expected={"may_depend_on": []},
    hint="Depend on a port instead.",
    docs_url="https://example.test/rules/SMT101",
    category=Category.DEPENDENCIES,
)

ROLE_VIOLATION = Violation(
    code="SMT303",
    rule="role-file",
    severity=Severity.WARNING,
    message="port Sessions must be defined in voice/application/ports.py",
    path="gw/features/voice/infra/sql.py",
    line=1,
    column=7,
    end_line=1,
    end_column=18,
    expected={"path": "voice/application/ports.py"},
    hint="Move Sessions to voice/application/ports.py and update its imports.",
    docs_url="https://example.test/rules/SMT303",
    category=Category.STRUCTURE,
)

HINT_VIOLATION = Violation(
    code="SMT305",
    rule="crowded-package",
    severity=Severity.HINT,
    message="voice/infra has 12 modules",
    path="gw/features/voice/infra/",
    docs_url="https://example.test/rules/SMT305",
    category=Category.STRUCTURE,
)

RULES = [
    RuleMeta(
        code="SMT101",
        name="layer-boundary",
        category=Category.DEPENDENCIES,
        default_severity=Severity.ERROR,
        enabled_by_default=True,
        summary="A module imports a layer it may not depend on.",
        docs_url="https://example.test/rules/SMT101",
    ),
    RuleMeta(
        code="SMT303",
        name="role-file",
        category=Category.STRUCTURE,
        default_severity=Severity.ERROR,
        enabled_by_default=True,
        summary="A class with a role lives in another module.",
        docs_url="https://example.test/rules/SMT303",
    ),
]


def read_line(path: str, line: int) -> str:
    return SOURCES.get(path, "").splitlines()[line - 1] if path in SOURCES else ""


@pytest.fixture
def report() -> Report:
    return Report(
        violations=[LAYER_VIOLATION, ROLE_VIOLATION, HINT_VIOLATION],
        modules=4,
        categories=[Category.DEPENDENCIES, Category.STRUCTURE],
        rules=RULES,
        suppressed=1,
        baselined=2,
    )


@pytest.fixture
def clean_report() -> Report:
    return Report(
        violations=[],
        modules=4,
        categories=[Category.DEPENDENCIES, Category.STRUCTURE],
        rules=RULES,
    )

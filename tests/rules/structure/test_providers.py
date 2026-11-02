from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smelt.engine.check import CheckOptions
from tests.helpers import violations, write_project

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
OPTIONS = CheckOptions(select=("SMT328",))
SOURCE = "from dishka import Provider, Scope, provide\nclass AdminProvider(Provider):\n    scope = Scope.REQUEST\n    @provide\n    def query(self, session: AsyncSession) -> AdminUserQuery:\n        return SqlAdminUserQuery(session)\n"


def _project(tmp_path: Path, source: str = SOURCE, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/providers.py": source},
    )


def test_constructor_forwarding_from_issue_64_is_reviewed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    [finding] = violations(root, OPTIONS)
    assert (finding.code, finding.line) == ("SMT328", 5)
    assert finding.expected is not None
    assert (
        finding.expected["suggested_registration"]
        == "provide(SqlAdminUserQuery, provides=AdminUserQuery)"
    )
    assert (root / "app/providers.py").read_text() == SOURCE


def test_keyword_dependencies_and_decorator_options_are_preserved(
    tmp_path: Path,
) -> None:
    source = (
        SOURCE.replace("@provide", "@provide(scope=Scope.REQUEST, cache=False)")
        .replace("session: AsyncSession", "session: AsyncSession, user: User")
        .replace(
            "SqlAdminUserQuery(session)",
            "SqlAdminUserQuery(user=user, session=session)",
        )
    )
    root = _project(tmp_path, source)
    [finding] = violations(root, OPTIONS)
    assert finding.expected is not None
    assert (
        finding.expected["suggested_registration"]
        == "provide(SqlAdminUserQuery, scope=Scope.REQUEST, cache=False, provides=AdminUserQuery)"
    )


@pytest.mark.parametrize(
    "source",
    [
        SOURCE.replace(
            "return SqlAdminUserQuery(session)",
            "return SqlAdminUserQuery(transform(session))",
        ),
        SOURCE.replace(
            "return SqlAdminUserQuery(session)", "yield SqlAdminUserQuery(session)"
        ),
        SOURCE.replace("def query", "async def query"),
        SOURCE.replace(
            "session: AsyncSession", "session: AsyncSession = default_session"
        ),
        SOURCE.replace("@provide", "@other\n    @provide"),
        SOURCE.replace("Provider):", "Provider, Mixin):"),
        SOURCE.replace("@provide", "@provide(when=condition)"),
        SOURCE.replace(
            "return SqlAdminUserQuery(session)", "return self.build(session)"
        ),
        SOURCE.replace(
            "return SqlAdminUserQuery(session)", "return SqlAdminUserQuery()"
        ),
        SOURCE.replace("class AdminProvider", "provide = custom\nclass AdminProvider"),
    ],
)
def test_nontrivial_or_uncertain_factories_are_preserved(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_aliases_and_opt_in_off_and_suppressions(tmp_path: Path) -> None:
    source = (
        SOURCE.replace(
            "from dishka import Provider, Scope, provide", "import dishka as di"
        )
        .replace("(Provider)", "(di.Provider)")
        .replace("Scope.REQUEST", "di.Scope.REQUEST")
        .replace("@provide", "@di.provide")
    )
    root = _project(tmp_path, source)
    assert violations(root) == []
    assert [v.code for v in violations(root, OPTIONS)] == ["SMT328"]
    _project(
        tmp_path,
        source,
        CONFIG
        + "structure:\n  contracts:\n    legacy: {match: app, rules: {redundant-dishka-provider: off}}\n",
    )
    assert violations(root, OPTIONS) == []
    _project(
        tmp_path,
        source.replace(
            "def query(self, session: AsyncSession) -> AdminUserQuery:",
            "def query(self, session: AsyncSession) -> AdminUserQuery:  # smelt: ignore[SMT328] -- Retains a framework factory contract.",
        ),
    )
    assert violations(root, OPTIONS) == []

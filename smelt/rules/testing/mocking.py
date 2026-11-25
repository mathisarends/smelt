from __future__ import annotations

from smelt.analysis.context import AnalysisContext, Index
from smelt.diagnostics.violation import Category
from smelt.rules.activation import POLICIES
from smelt.rules.base import GuideTarget, Rule, RuleDoc


class ForbiddenTestMock(Rule):
    scope = "test"
    activation = POLICIES["TST003"]
    code = "TST003"
    name = "tests/forbidden-mock"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A recognized mocking operation occurs in a test path that forbids mocks.",
        rationale="Domain tests can exercise real domain objects and injected fakes. This opt-in path policy rejects recognized unittest.mock constructors/patching, standard pytest-mock operations and pytest.MonkeyPatch setattr/delattr replacements, including setup fixtures. MonkeyPatch receivers can be the standard fixture, annotated instances, constructors, aliases or context() scopes. MonkeyPatch setenv/delenv, mapping and directory setup are not counted as collaborator replacements. It does not infer arbitrary test doubles or traverse fixture dependencies.",
        bad="from unittest.mock import Mock\ndef test_rule():\n    service = Mock()",
        good="def test_rule():\n    result = calculate_total([10, 20])\n    assert result == 30",
        fix="Use real domain objects or a small fake implementing the port. Keep boundary mocking in explicitly permitted test paths; use a reasoned suppression for an intentional exception.",
        canonical="Use real objects or small fakes instead of mocks",
        config=("tests.forbidden_mock_paths", "tests.quality_exclude"),
    )


class StringPatchTarget(Rule):
    scope = "test"
    code = "TST004"
    name = "tests/string-patch-target"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A recognized patch call or decorator uses a literal string target.",
        rationale="String patch targets encode import paths that refactoring tools may not update. This opt-in convention checks unittest.mock and standard pytest-mock patch, patch.dict and patch.multiple literal targets, plus the dotted-target forms of pytest.MonkeyPatch setattr/delattr. Object/attribute forms are distinguished by their argument shape. It does not claim that object-based patching removes implementation coupling; variables and dynamically computed targets are outside this syntax check.",
        bad='from unittest.mock import patch\n@patch("app.service.Repository")\ndef test_run(repository): ...',
        good="def test_run():\n    repository = InMemoryRepository()\n    run(repository)\n    assert repository.saved == [42]",
        fix="Prefer an injected collaborator/fake. For deliberate boundary patching, use object-based patching where appropriate and patch the name looked up by the caller. No automatic rewrite is performed.",
        canonical="Inject fakes or patch objects instead of string targets",
        config=("rules", "tests.quality_exclude"),
    )


class ExcessiveTestPatches(Rule):
    scope = "test"
    activation = POLICIES["TST005"]
    code = "TST005"
    name = "tests/excess-patches"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A collected test exceeds its configured number of recognized patch calls.",
        rationale="Many replacements can indicate a test coupled to construction and internal calls. Count patch operations in decorators and the test body once per syntactic call, including patch.object. Nested helper bodies, fixtures and transitive calls are not attributed to callers. The limit is a configurable convention, not a proof that a test is bad.",
        bad='def test_run(mocker):\n    mocker.patch("app.one")\n    mocker.patch("app.two")',
        good="def test_run():\n    result = run(InMemoryRepository())\n    assert result.status == 'active'",
        fix="Reduce the scenario's dependencies or inject a fake. Keep a justified integration scenario with a reasoned exception rather than hiding patches inside helpers.",
        canonical="Keep patches per test within the limit; inject fakes instead",
        config=("rules", "tests.quality_exclude"),
    )

    def guide(self, ctx: AnalysisContext, target: GuideTarget) -> str | None:
        del target
        maximum = ctx.config.max_patches_per_test
        if maximum is None:
            return None
        if maximum == 0:
            return "Do not patch in tests; inject fakes instead"
        return f"Use at most {maximum} patches per test; inject fakes instead"

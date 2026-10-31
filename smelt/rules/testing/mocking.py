from __future__ import annotations

import ast
from collections import Counter
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.config.patterns import path_matches
from smelt.diagnostics.violation import Category, Violation
from smelt.rules.activation import POLICIES
from smelt.rules.base import BaseRule, RuleDoc

if TYPE_CHECKING:
    from collections.abc import Iterator


class ForbiddenTestMock(BaseRule):
    test_local = True
    scope = "test"
    activation = POLICIES["SMT421"]
    code = "SMT421"
    name = "forbidden-test-mock"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A recognized mocking operation occurs in a test path that forbids mocks.",
        rationale="Domain tests can exercise real domain objects and injected fakes. This opt-in path policy rejects recognized unittest.mock constructors/patching, standard pytest-mock operations and pytest.MonkeyPatch setattr/delattr replacements, including setup fixtures. MonkeyPatch receivers can be the standard fixture, annotated instances, constructors, aliases or context() scopes. MonkeyPatch setenv/delenv, mapping and directory setup are not counted as collaborator replacements. It does not infer arbitrary test doubles or traverse fixture dependencies.",
        bad="from unittest.mock import Mock\ndef test_rule():\n    service = Mock()",
        good="def test_rule():\n    result = calculate_total([10, 20])\n    assert result == 30",
        fix="Use real domain objects or a small fake implementing the port. Keep boundary mocking in explicitly permitted test paths; use a reasoned suppression for an intentional exception.",
        config=("tests.forbid_mocks_in", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        patterns = ctx.config.tests.forbid_mocks_in
        for module in ctx.test_syntax.modules.values():
            if not any(path_matches(pattern, module.path) for pattern in patterns):
                continue
            for call in module.calls:
                if call.mock:
                    yield self.violation(
                        f"mock operation {call.qualified} is forbidden in this test path",
                        path=module.path,
                        line=call.node.lineno,
                        column=call.node.col_offset + 1,
                        expected={
                            "forbid_mocks_in": patterns,
                            "operation": call.qualified,
                            "family": call.family,
                            "provenance": call.provenance,
                        },
                        hint="Exercise the domain with real values and an injected fake instead of a mock.",
                    )


class StringPatchTarget(BaseRule):
    test_local = True
    scope = "test"
    code = "SMT422"
    name = "string-patch-target"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A recognized patch call or decorator uses a literal string target.",
        rationale="String patch targets encode import paths that refactoring tools may not update. This opt-in convention checks unittest.mock and standard pytest-mock patch, patch.dict and patch.multiple literal targets, plus the dotted-target forms of pytest.MonkeyPatch setattr/delattr. Object/attribute forms are distinguished by their argument shape. It does not claim that object-based patching removes implementation coupling; variables and dynamically computed targets are outside this syntax check.",
        bad='from unittest.mock import patch\n@patch("app.service.Repository")\ndef test_run(repository): ...',
        good="def test_run():\n    repository = InMemoryRepository()\n    run(repository)\n    assert repository.saved == [42]",
        fix="Prefer an injected collaborator/fake. For deliberate boundary patching, use object-based patching where appropriate and patch the name looked up by the caller. No automatic rewrite is performed.",
        config=("rules", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        for module in ctx.test_syntax.modules.values():
            for call in module.calls:
                if call.string_target:
                    yield self.violation(
                        "patch uses a literal string target",
                        path=module.path,
                        line=call.node.lineno,
                        column=call.node.col_offset + 1,
                        expected={
                            "operation": call.qualified,
                            "family": call.family,
                            "provenance": call.provenance,
                        },
                        hint="Inject the collaborator, or review deliberate object-based boundary patching.",
                    )


class ExcessiveTestPatches(BaseRule):
    test_local = True
    scope = "test"
    activation = POLICIES["SMT423"]
    code = "SMT423"
    name = "excessive-test-patches"
    category = Category.TESTS
    requires = frozenset({Index.TESTS})
    doc = RuleDoc(
        summary="A collected test exceeds its configured number of recognized patch calls.",
        rationale="Many replacements can indicate a test coupled to construction and internal calls. Count patch operations in decorators and the test body once per syntactic call, including patch.object. Nested helper bodies, fixtures and transitive calls are not attributed to callers. The limit is a configurable convention, not a proof that a test is bad.",
        bad='def test_run(mocker):\n    mocker.patch("app.one")\n    mocker.patch("app.two")',
        good="def test_run():\n    result = run(InMemoryRepository())\n    assert result.status == 'active'",
        fix="Reduce the scenario's dependencies or inject a fake. Keep a justified integration scenario with a reasoned exception rather than hiding patches inside helpers.",
        config=("tests.max_patches_per_test", "tests.quality_exclude"),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        maximum = ctx.config.tests.max_patches_per_test
        if maximum is None:
            return
        for module in ctx.test_syntax.modules.values():
            for test in module.tests:
                patches = [call for call in test.calls if call.patch]
                if len(patches) > maximum:
                    counts: Counter[str] = Counter()
                    edges: list[str] = []
                    for call in patches:
                        shape = ast.dump(call.node, include_attributes=False)
                        edges.append(f"{test.name}:{shape}#{counts[shape]}")
                        counts[shape] += 1
                    yield self.violation(
                        f"{test.name} has {len(patches)} patch calls; maximum is {maximum}",
                        path=module.path,
                        line=test.node.lineno,
                        expected={
                            "test": test.name,
                            "patch_edges": edges,
                            "max_patches_per_test": maximum,
                            "patches": [
                                {
                                    "line": call.node.lineno,
                                    "operation": call.qualified,
                                    "family": call.family,
                                    "provenance": call.provenance,
                                }
                                for call in patches
                            ],
                        },
                        hint="Exercise a smaller behavior or inject a fake instead of replacing internal collaborators.",
                    )

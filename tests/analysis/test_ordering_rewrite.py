import ast
import textwrap

import pytest

from smelt.analysis.ordering import analyze_order
from smelt.analysis.ordering_rewrite import rewrite_order
from smelt.analysis.parsing import AnalysisError
from smelt.config.models import OrderingConfig


def _rewrite(source: str) -> str:
    result = analyze_order(ast.parse(source), OrderingConfig())
    sections = [
        section
        for section in result.sections
        if section.edges and section.fix_reason is None
    ]
    return rewrite_order(source, result, sections)


def test_comments_and_blank_lines_travel_with_the_definition() -> None:
    source = "import sys\n\n# Helper contract.\ndef _helper():\n    return 1\n\n\n# API contract.\ndef api():\n    return _helper()\n"

    rewritten = _rewrite(source)

    assert "# API contract.\ndef api():" in rewritten
    assert "# Helper contract.\ndef _helper():" in rewritten
    assert rewritten.index("def api") < rewritten.index("def _helper")
    assert rewritten.startswith("import sys\n")
    assert _rewrite(rewritten) == rewritten


def test_preamble_shebang_coding_and_file_pragmas_stay_at_the_top() -> None:
    source = "#!/usr/bin/env python\n# coding: utf-8\n# ruff: noqa\n# type: ignore\n# helper\ndef _helper(): pass\n\n# public\ndef api(): pass\n"

    rewritten = _rewrite(source)

    assert rewritten.startswith(
        "#!/usr/bin/env python\n# coding: utf-8\n# ruff: noqa\n# type: ignore\n"
    )
    assert "# helper\ndef _helper" in rewritten
    assert "# public\ndef api" in rewritten


def test_indented_trailing_comments_stay_inside_their_definition() -> None:
    source = "def _helper():\n    value = 1\n    # Keep this explanation with helper.\n\ndef api():\n    return 2\n"

    rewritten = _rewrite(source)

    assert rewritten.index("def api") < rewritten.index("def _helper")
    assert "    value = 1\n    # Keep this explanation with helper." in rewritten


def test_multiline_class_headers_are_preserved() -> None:
    source = (
        "class Service(\n):\n    def _helper(self): pass\n    def api(self): pass\n"
    )

    rewritten = _rewrite(source)

    assert rewritten.startswith("class Service(\n):\n")
    assert rewritten.index("def api") < rewritten.index("def _helper")


def test_nested_class_and_module_edits_are_rendered_together() -> None:
    source = "def _global(): pass\n\nclass Service:\n    def _helper(self): pass\n    def api(self): pass\n"

    rewritten = _rewrite(source)

    assert rewritten.index("class Service") < rewritten.index("def _global")
    assert rewritten.index("def api") < rewritten.index("def _helper")
    assert _rewrite(rewritten) == rewritten


def test_nested_class_scope_does_not_require_moving_its_enclosing_class() -> None:
    source = "class Outer:\n    class Inner:\n        def _helper(self): pass\n        def api(self): pass\n"

    rewritten = _rewrite(source)

    assert rewritten.startswith("class Outer:\n    class Inner:\n")
    assert rewritten.index("def api") < rewritten.index("def _helper")


def test_overload_decorators_stay_with_their_implementation() -> None:
    source = "from typing import overload\n@overload\ndef _helper(value: int): ...\n@overload\ndef _helper(value: str): ...\ndef _helper(value): return value\n\ndef api(): pass\n"

    rewritten = _rewrite(source)

    assert rewritten.index("def api") < rewritten.index("@overload")
    assert rewritten.count("def _helper") == 3
    assert "@overload\ndef _helper(value: int)" in rewritten
    assert "@overload\ndef _helper(value: str)" in rewritten


def test_property_family_moves_as_one_block() -> None:
    source = "class Service:\n    def _helper(self): pass\n\n    @property\n    def value(self): return 1\n\n    @value.setter\n    def value(self, new): pass\n\n    @value.deleter\n    def value(self): pass\n"

    rewritten = _rewrite(source)

    assert (
        rewritten.index("@property")
        < rewritten.index("@value.setter")
        < rewritten.index("@value.deleter")
        < rewritten.index("def _helper")
    )


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("final_newline", [False, True])
def test_newline_convention_and_missing_final_newline_are_preserved(
    newline: str, final_newline: bool
) -> None:
    source = newline.join(["def _helper(): pass", "def api(): pass"]) + (
        newline if final_newline else ""
    )

    rewritten = _rewrite(source)

    assert rewritten.startswith("def api")
    assert rewritten.endswith(newline) is final_newline
    assert "\n" not in rewritten.replace(newline, "")
    ast.parse(rewritten)


def test_string_contents_are_never_reformatted() -> None:
    source = 'def _helper():\n    return """text\n    # Not a comment\n    café"""\n\ndef api(): pass\n'

    rewritten = _rewrite(source)

    assert '"""text\n    # Not a comment\n    café"""' in rewritten


@pytest.mark.parametrize(
    "pragma", ["# fmt: off", "#fmt: off", "# fmt: skip", "# pylint: disable=all"]
)
def test_region_pragmas_require_manual_review(pragma: str) -> None:
    source = f"{pragma}\ndef _helper(): pass\ndef api(): pass\n"

    with pytest.raises(AnalysisError, match="region"):
        _rewrite(source)


def test_compiler_only_errors_prevent_source_rewrites() -> None:
    source = "def _helper(): pass\ndef api(): pass\nreturn 3\n"

    with pytest.raises(AnalysisError, match="compiled"):
        _rewrite(source)


def test_source_changes_after_analysis_are_rejected() -> None:
    source = "def _helper(): pass\ndef api(): pass\n"
    result = analyze_order(ast.parse(source), OrderingConfig())

    with pytest.raises(AnalysisError, match="changed"):
        rewrite_order(
            source.replace("pass", "return 1"),
            result,
            [section for section in result.sections if section.edges],
        )


@pytest.mark.parametrize(
    ("source", "expression", "value"),
    [
        ("def _helper(): return 7\ndef api(): return _helper()", "api()", 7),
        (
            "_DEFAULT = 7\ndef _unused(): return 3\ndef api(value=_DEFAULT): return value",
            "api()",
            7,
        ),
        (
            "class Service:\n    def _helper(self): return self.value\n    def api(self): return self._helper()\n    def __init__(self): self.value = 9",
            "Service().api()",
            9,
        ),
    ],
)
def test_trusted_fixtures_keep_their_runtime_behavior(
    source: str, expression: str, value: int
) -> None:
    original = textwrap.dedent(source)
    rewritten = _rewrite(original)
    assert rewritten != original
    for code in (original, rewritten):
        namespace: dict[str, object] = {}
        exec(code, namespace)  # noqa: S102 -- execute only these fixed test fixtures.
        assert eval(expression, namespace) == value  # noqa: S307 -- fixed test expression.

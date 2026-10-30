import ast
import textwrap

import pytest
from pydantic import ValidationError

from smelt.analysis.ordering import FileOrder, analyze_order
from smelt.config.models import OrderingConfig


def _analyze(
    source: str, *, deferred: bool = False, config: OrderingConfig | None = None
) -> FileOrder:
    return analyze_order(
        ast.parse(textwrap.dedent(source)),
        config or OrderingConfig(),
        deferred=deferred,
    )


def test_public_first_is_stable_within_each_group() -> None:
    result = _analyze(
        "def _first(): pass\ndef public_first(): return _first()\ndef _second(): pass\ndef public_second(): return _second()"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.expected == [
        "public_first",
        "public_second",
        "_first",
        "_second",
    ]


@pytest.mark.parametrize(
    "source",
    [
        "def _helper(): pass\ndef api(value=_helper): pass",
        "_DEFAULT = 3\ndef api(value=_DEFAULT): pass",
        "_T = int\ndef api(value: _T): pass",
        "_DEFAULT = 3\nPUBLIC = _DEFAULT",
        "def _register(fn): return fn\n@_register\ndef api(): pass",
        "class _Base: pass\nclass Public(_Base): pass",
        "class _Meta(type): pass\nclass Public(metaclass=_Meta): pass",
        "_DEFAULT = 3\nPUBLIC = _DEFAULT * 2",
    ],
)
def test_required_prerequisites_are_not_reported(source: str) -> None:
    assert _analyze(source).issues == []


def test_defaults_stay_eager_with_deferred_annotations() -> None:
    assert (
        _analyze("_T = int\ndef api(value: _T = _T): pass", deferred=True).issues == []
    )


def test_annotation_dependencies_follow_target_semantics() -> None:
    source = "_T = int\ndef api(value: _T): pass"

    assert _analyze(source).issues == []
    deferred = _analyze(source, deferred=True)
    assert deferred.issues[0].section is not None
    assert deferred.issues[0].section.expected == ["api", "_T"]


def test_quoted_annotations_and_lambda_bodies_are_not_eager() -> None:
    result = _analyze(
        "def _helper(): pass\ndef api(value: '_helper' = lambda: _helper()): pass"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.expected == ["api", "_helper"]


@pytest.mark.parametrize(
    "barrier",
    [
        "register()",
        "if FLAG:\n    prepare()",
        "try:\n    prepare()\nexcept Exception:\n    pass",
        "if TYPE_CHECKING:\n    from module import Type",
        "VALUE = factory()",
        "VALUE = left + right",
    ],
)
def test_barriers_are_not_crossed(barrier: str) -> None:
    result = _analyze(f"def _helper(): pass\n{barrier}\ndef api(): pass\n")

    assert result.issues == []
    assert result.barriers


def test_rebinding_preserves_which_value_a_default_reads() -> None:
    result = _analyze(
        "_T = 1\ndef api(value=_T): pass\n_T = 2\ndef later(value=_T): pass"
    )

    assert result.issues == []


def test_forward_eager_reads_are_not_silently_repaired_or_rebound() -> None:
    assert _analyze("def _helper(value=PUBLIC): pass\nPUBLIC = 2").issues == []


def test_class_body_reads_protect_module_prerequisites() -> None:
    assert _analyze("_DEFAULT = 3\nclass Public:\n    value = _DEFAULT").issues == []


def test_class_locals_do_not_create_false_module_dependencies() -> None:
    result = _analyze(
        "_DEFAULT = 3\nclass Public:\n    _DEFAULT = 7\n    def method(self, value=_DEFAULT): pass"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.expected == ["Public", "_DEFAULT"]


def test_class_category_order() -> None:
    result = _analyze(
        "class Service:\n    def _helper(self): pass\n    def api(self): pass\n    def __str__(self): return 'service'\n    def __init__(self): pass\n    count = 0"
    )

    section = next(
        issue.section for issue in result.issues if issue.scope.endswith("Service#0")
    )
    assert section is not None
    assert section.expected == ["count", "__init__", "__str__", "api", "_helper"]


def test_class_attribute_can_depend_on_an_earlier_private_method() -> None:
    result = _analyze(
        "class Service:\n    def _build(): pass\n    value = _build\n    def api(self): pass"
    )

    assert result.issues == []


@pytest.mark.parametrize(
    ("statement", "decorator"),
    [
        ("from typing import overload", "overload"),
        ("from typing import overload as ov", "ov"),
        ("import typing as t", "t.overload"),
    ],
)
def test_overload_family_is_atomic(statement: str, decorator: str) -> None:
    result = _analyze(
        f"{statement}\n@{decorator}\ndef _helper(value: int): ...\n@{decorator}\ndef _helper(value: str): ...\ndef _helper(value): return value\ndef api(): pass"
    )

    section = result.issues[0].section
    assert section is not None
    assert section.expected == ["api", "_helper"]
    assert len(section.blocks[0].nodes) == 3


def test_property_family_is_atomic() -> None:
    result = _analyze(
        "class Service:\n    def _helper(self): pass\n    @property\n    def value(self): return 1\n    @value.setter\n    def value(self, new): pass\n    @value.deleter\n    def value(self): pass"
    )

    section = result.issues[0].section
    assert section is not None
    assert section.expected == ["value", "_helper"]
    assert len(section.blocks[1].nodes) == 3


def test_typing_guards_keep_known_builtin_decorators() -> None:
    result = _analyze(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from other import Type\nclass Service:\n    def _helper(self): pass\n    @property\n    def value(self): return 1"
    )

    assert any(
        issue.section and issue.section.expected == ["value", "_helper"]
        for issue in result.issues
    )


def test_noncontiguous_properties_are_pinned() -> None:
    result = _analyze(
        "class Service:\n    @property\n    def value(self): return 1\n    def _helper(self): pass\n    @value.setter\n    def value(self, new): pass"
    )

    assert result.issues == []
    assert any("noncontiguous property" in item["reason"] for item in result.barriers)


@pytest.mark.parametrize(
    "setup",
    ["property = factory()", "from external import property", "from external import *"],
)
def test_shadowed_decorators_are_not_assumed_safe(setup: str) -> None:
    result = _analyze(
        f"{setup}\nclass Service:\n    def _helper(self): pass\n    @property\n    def value(self): return 1"
    )

    assert not any(
        issue.section and issue.section.expected == ["value", "_helper"]
        for issue in result.issues
    )


def test_type_variables_have_a_separate_category() -> None:
    result = _analyze(
        "from typing import TypeVar as TV\ndef api(): pass\n_T = TV('_T')"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.expected == ["_T", "api"]


def test_constants_can_be_exempted() -> None:
    source = "_LIMIT = 3\ndef api(): pass\ndef _helper(): pass"

    assert _analyze(source).issues
    assert _analyze(source, config=OrderingConfig(constants_exempt=True)).issues == []


def test_main_guard_needs_manual_review_when_not_last() -> None:
    result = _analyze("if __name__ == '__main__':\n    main()\ndef api(): pass")

    assert result.issues[0].section is None
    assert "main guard" in result.issues[0].message


def test_late_imports_are_not_automatically_moved() -> None:
    result = _analyze("def api(): pass\nimport sys")

    assert result.issues[0].section is None
    assert "execution barriers" in (result.issues[0].reason or "")


def test_inherited_classes_are_diagnosed_but_not_automatically_fixed() -> None:
    result = _analyze(
        "class Service(Base):\n    def _helper(self): pass\n    def api(self): pass"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.fix_reason


def test_function_bodies_are_outside_ordering_scope() -> None:
    assert (
        _analyze("def api():\n    def _helper(): pass\n    def public(): pass").issues
        == []
    )


@pytest.mark.parametrize(
    "setup",
    [
        "from typing import TypeVar\nVALUE = (TypeVar := replacement)",
        "import typing\ntyping.TypeVar = replacement",
    ],
)
def test_mutated_type_factories_are_not_trusted(setup: str) -> None:
    factory = "typing.TypeVar" if "import typing" in setup else "TypeVar"
    source = f"{setup}\ndef api(): pass\n_T = {factory}('_T')"

    assert _analyze(source).issues == []


def test_type_variable_arguments_with_calls_are_barriers() -> None:
    source = "from typing import TypeVar\ndef api(): pass\n_T = TypeVar('_T', bound=make_bound())"

    assert _analyze(source).issues == []


def test_global_rebinding_requires_manual_fix_review() -> None:
    result = _analyze(
        "def _helper():\n    global VALUE\n    VALUE = 1\ndef api(): pass"
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.fix_reason


def test_diagnostic_points_at_the_first_misplaced_definition() -> None:
    result = _analyze("PUBLIC = 1\ndef _helper(): pass\ndef api(): pass")

    assert result.issues[0].line == 2


def test_class_priority_can_be_configured() -> None:
    config = OrderingConfig.model_validate(
        {"class": ["class-attributes", "init", "public", "dunders", "private"]}
    )
    result = _analyze(
        "class Service:\n    def __str__(self): pass\n    def api(self): pass",
        config=config,
    )

    assert result.issues[0].section is not None
    assert result.issues[0].section.expected == ["api", "__str__"]


def test_enabled_is_not_an_ordering_option() -> None:
    with pytest.raises(ValidationError, match="enabled"):
        OrderingConfig.model_validate({"enabled": False})

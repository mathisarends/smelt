import ast

import pytest

from smelt.analysis.none_defaults import none_defaults


@pytest.mark.parametrize("annotation", ["Optional[Options]", "T.Optional[Options]"])
def test_unresolved_typing_lookalikes_are_not_assumed_optional(annotation: str) -> None:
    tree = ast.parse(
        f"def create(options: {annotation} = None):\n    return options or fallback()"
    )
    assert (
        list(none_defaults(tree, {"Optional": "custom.Optional", "T": "custom"})) == []
    )


def test_direct_bool_call_is_a_truthiness_test() -> None:
    tree = ast.parse(
        "def create(options: int | None = None):\n    return bool(options or ready())"
    )
    assert list(none_defaults(tree, {})) == []

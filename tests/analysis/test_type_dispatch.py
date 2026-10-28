import ast

import pytest

from smelt.analysis.type_dispatch import type_dispatches

CHAIN = """if isinstance(event, Started):
    start(event)
elif isinstance(event, Delta | Progress):
    update(event)
elif isinstance(event, Finished) and event.ok:
    finish(event)
"""


def test_union_types_guards_and_else_are_preserved_as_evidence() -> None:
    [candidate] = type_dispatches(ast.parse(CHAIN + "else:\n    fallback(event)"), 3)
    assert candidate.subject == "event"
    assert candidate.node.lineno == 1
    assert candidate.branches[1].types == ("Delta", "Progress")
    assert candidate.branches[2].guard == "event.ok"


def test_independent_observation_and_inner_condition_are_not_merged_or_hoisted() -> (
    None
):
    tree = ast.parse("""async def stream(raw):
    async for event in raw:
        if isinstance(event, SessionEvent):
            events.append(event)
        if isinstance(event, OutputValidated | OutputValidationFailed):
            if event.call_id is not None:
                control_calls.add(event.call_id)
        elif isinstance(event, TextDelta | ReasoningDelta | ApprovalNeeded):
            yield event
        elif isinstance(event, ToolCalled) and event.call.id not in control_calls:
            calls[event.call.id] = event.call
            yield ToolStarted(event.call)
        elif isinstance(event, ToolProgress) and event.call_id in calls:
            yield event
        elif isinstance(event, ToolReturned) and event.call_id in calls:
            yield ToolFinished(calls[event.call_id], event.result)
        elif isinstance(event, TurnStart | AgentControlChanged):
            yield event
""")
    [candidate] = type_dispatches(tree, 3)
    assert candidate.node.lineno == 5
    assert len(candidate.branches) == 6
    assert candidate.branches[0].guard is None
    assert candidate.branches[2].guard == "event.call.id not in control_calls"
    assert isinstance(candidate.node.body[0], ast.If)


@pytest.mark.parametrize(
    ("imports", "checker"),
    [
        ("from builtins import isinstance as check\n", "check"),
        ("import builtins as b\n", "b.isinstance"),
        ("import builtins\n", "builtins.isinstance"),
        ("from builtins import isinstance\n", "isinstance"),
    ],
)
def test_top_level_builtin_aliases(imports: str, checker: str) -> None:
    assert (
        len(
            list(
                type_dispatches(
                    ast.parse(imports + CHAIN.replace("isinstance", checker)), 3
                )
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "isinstance = custom\n",
        "from custom import isinstance\n",
        "from custom import *\n",
        "def unrelated(isinstance): pass\n",
        "class isinstance: pass\n",
        "for isinstance in values: pass\n",
        "try:\n    run()\nexcept Error as isinstance:\n    pass\n",
        "match value:\n    case isinstance:\n        pass\n",
        "import builtins\nbuiltins.isinstance = custom\n",
        "import builtins\ndel builtins.isinstance\n",
    ],
)
def test_shadowed_or_mutated_checkers_are_conservatively_excluded(prefix: str) -> None:
    assert list(type_dispatches(ast.parse(prefix + CHAIN), 3)) == []


def test_later_binding_is_not_mistaken_for_a_builtin_inside_a_function() -> None:
    source = (
        "def dispatch(event):\n"
        + "\n".join("    " + line for line in CHAIN.splitlines())
        + "\nisinstance = custom"
    )
    assert list(type_dispatches(ast.parse(source), 3)) == []


@pytest.mark.parametrize(
    "source",
    [
        CHAIN.replace("isinstance(event, Finished)", "ready"),
        CHAIN.replace("isinstance(event, Finished)", "isinstance(other, Finished)"),
        CHAIN.replace("isinstance(event, Finished)", "not isinstance(event, Finished)"),
        CHAIN.replace("isinstance(event, Finished)", "isinstance(event, factory())"),
        CHAIN.replace("isinstance(event, Finished)", "isinstance(event, list[int])"),
        CHAIN.replace("isinstance(event, Finished)", "isinstance(event, ())"),
        CHAIN.replace(
            "isinstance(event, Finished)", "isinstance(event, Finished) or ready"
        ),
        CHAIN.replace("event.ok", "(event := other)"),
        CHAIN.replace("event,", "holder.event,"),
        CHAIN.replace("event,", "load(),"),
        CHAIN.replace("elif", "if"),
        CHAIN.replace("isinstance", "custom.isinstance"),
    ],
)
def test_unsupported_chains_are_not_partially_reported(source: str) -> None:
    assert list(type_dispatches(ast.parse(source), 3)) == []


def test_threshold_counts_branches_not_union_members() -> None:
    assert list(type_dispatches(ast.parse(CHAIN), 4)) == []
    assert len(list(type_dispatches(ast.parse(CHAIN), 3))) == 1


def test_nested_tuple_types_are_flattened() -> None:
    source = CHAIN.replace("Delta | Progress", "(events.Delta, (events.Progress,))")
    [candidate] = type_dispatches(ast.parse(source), 3)
    assert candidate.branches[1].types == ("events.Delta", "events.Progress")


def test_multiple_and_guards_keep_their_order() -> None:
    source = CHAIN.replace("event.ok", "event.ok and ready(event)")
    [candidate] = type_dispatches(ast.parse(source), 3)
    assert candidate.branches[2].guard == "event.ok and ready(event)"

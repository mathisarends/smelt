from pathlib import Path

import pytest

from smelt.config import calls
from smelt.config.errors import ConfigError
from smelt.config.project_rules import Rule, load_project_rules
from smelt.config.source import SourceStore
from tests.helpers import write_project

_CLOCK = """
from smelt.rules import Rule, calls

clock = Rule(
    name="acme/domain-clock",
    match=calls.to("datetime.datetime.now").in_layer("domain"),
    message="Direct clock read",
    guide="Use acme.clock.now().",
)
rules = [clock]
"""

_GENERATED = """
from smelt.rules import Rule, calls

_CLOCKS = {"clock": "datetime.datetime.now", "timer": "time.time"}


def _forbid(name, target):
    return Rule(
        name=f"acme/domain-{name}",
        match=calls.to(target).in_layer("domain"),
        message=f"Direct {name} read",
        guide="Use acme.clock.",
    )


rules = [_forbid(name, target) for name, target in _CLOCKS.items()]
"""


class _Snapshot(SourceStore):
    """A store whose rule module differs from the file on disk."""

    def __init__(self, source: str) -> None:
        self._source = source

    def read_bytes(self, path: Path) -> bytes:
        return self._source.encode()


def _load(
    root: Path, *paths: str, store: SourceStore | None = None
) -> tuple[Rule, ...]:
    return load_project_rules(
        list(paths), root / "smelt.yml", root=root, store=store or SourceStore()
    )


def test_loads_the_exported_rules(tmp_path: Path) -> None:
    root = write_project(tmp_path, {"tools/rules.py": _CLOCK})

    assert _load(root, "tools/rules.py") == (
        Rule(
            name="acme/domain-clock",
            match=calls.to("datetime.datetime.now").in_layer("domain"),
            message="Direct clock read",
            guide="Use acme.clock.now().",
        ),
    )


def test_runs_helpers_and_loops_of_the_module(tmp_path: Path) -> None:
    root = write_project(tmp_path, {"rules.py": _GENERATED})

    loaded = _load(root, "rules.py")

    assert [(rule.name, rule.match.target) for rule in loaded] == [
        ("acme/domain-clock", "datetime.datetime.now"),
        ("acme/domain-timer", "time.time"),
    ]


def test_reads_the_module_from_the_source_store(tmp_path: Path) -> None:
    root = write_project(tmp_path, {"rules.py": "rules = []\n"})

    loaded = _load(root, "rules.py", store=_Snapshot(_CLOCK))

    assert [rule.name for rule in loaded] == ["acme/domain-clock"]


def test_module_output_stays_off_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_project(tmp_path, {"rules.py": "print('loading')\nrules = []\n"})

    assert _load(root, "rules.py") == ()

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "loading\n")


@pytest.mark.parametrize(
    ("contents", "error"),
    [
        ("answer = 42\n", "expected a module-level `rules` list"),
        ("rules = ['acme/domain-clock']\n", "expected a module-level `rules` list"),
        ("rules = [\n", "line 1: SyntaxError"),
        ("import os\n\nraise RuntimeError('boom')\n", "line 3: RuntimeError: boom"),
        (
            _CLOCK.replace("acme/domain-clock", "DomainClock"),
            "line 3: ValueError: expected a qualified rule name",
        ),
        (
            _CLOCK.replace('.in_layer("domain")', "").replace(
                'calls.to("datetime.datetime.now")', '"datetime.datetime.now"'
            ),
            "TypeError: acme/domain-clock: expected match=calls.to(...)",
        ),
        (
            _CLOCK.replace("datetime.datetime.now", "now"),
            "ValueError: expected a qualified call target",
        ),
        (
            _CLOCK.replace("Direct clock read", " "),
            "ValueError: acme/domain-clock: expected a non-empty message",
        ),
        (
            _CLOCK.replace("[clock]", "[clock, clock]"),
            "duplicate rule 'acme/domain-clock', first declared in rules.py",
        ),
    ],
)
def test_reports_an_invalid_module_as_a_plugins_issue(
    tmp_path: Path, contents: str, error: str
) -> None:
    root = write_project(tmp_path, {"rules.py": contents})

    with pytest.raises(ConfigError) as raised:
        _load(root, "rules.py")

    issue = raised.value.issues[0]
    assert issue.path == "plugins"
    assert issue.message.startswith("rules.py: ")
    assert error in issue.message


def test_names_the_first_module_of_a_rule_declared_twice(tmp_path: Path) -> None:
    root = write_project(tmp_path, {"first.py": _CLOCK, "second.py": _CLOCK})

    with pytest.raises(ConfigError) as raised:
        _load(root, "first.py", "second.py")

    assert raised.value.issues[0].message == (
        "second.py: duplicate rule 'acme/domain-clock', first declared in first.py"
    )


@pytest.mark.parametrize(
    ("relative", "error"),
    [
        ("../outside.py", "expected a project-relative .py file"),
        ("rules.txt", "expected a project-relative .py file"),
        ("/rules.py", "expected a project-relative .py file"),
        ("C:/rules.py", "expected a project-relative .py file"),
        ("missing.py", "file not found"),
    ],
)
def test_rejects_a_path_that_is_not_a_project_python_file(
    tmp_path: Path, relative: str, error: str
) -> None:
    root = write_project(
        tmp_path / "project", {"rules.txt": _CLOCK, "../outside.py": _CLOCK}
    )

    with pytest.raises(ConfigError) as raised:
        _load(root, relative)

    assert raised.value.issues[0].message == f"{relative}: {error}"

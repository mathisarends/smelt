import pytest

from smelt.diagnostics.render.evidence import decision, subject_name
from smelt.diagnostics.violation import Severity, Violation

EXPECTED = {
    "class": "Session",
    "method": "add",
    "names": ["Parse", "Load"],
    "automatic_rewrite": False,
    "evidence": {"type": "app.Client", "path": "self._client", "missing": None},
}


def _finding(expected: dict[str, object] | None) -> Violation:
    return Violation(
        code="CPX001",
        rule="complexity/excess-attribute-depth",
        severity=Severity.HINT,
        message="review the call",
        expected=expected,
    )


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("{class}.{method}", "Session.add"),
        ("{names} via {evidence.type}", "Parse, Load via app.Client"),
        ("{class} in {owner}", ""),
        ("{evidence.missing}", ""),
        ("{automatic_rewrite}", ""),
        ("{evidence}", ""),
        ("no key", ""),
    ],
)
def test_subject_fills_every_key_or_stays_empty(template: str, name: str) -> None:
    assert subject_name(_finding(EXPECTED), template) == name


def test_subject_of_a_finding_without_evidence_is_empty() -> None:
    assert subject_name(_finding(None), "{class}") == ""


@pytest.mark.parametrize(
    ("keys", "label"),
    [
        (("evidence.type", "evidence.path"), "Type: app.Client"),
        (("evidence.missing", "evidence.path"), "Path: self._client"),
        (("automatic_rewrite",), ""),
        (("owner",), ""),
        ((), ""),
    ],
)
def test_decision_is_labelled_by_the_first_key_present(
    keys: tuple[str, ...], label: str
) -> None:
    assert decision(_finding(EXPECTED), keys) == label

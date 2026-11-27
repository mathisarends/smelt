from dataclasses import replace

from smelt.diagnostics.render.rule_view import RuleBrief, render_rule_view
from smelt.diagnostics.report import ConfigurationDiagnostic, Report
from smelt.diagnostics.violation import Category, Severity, Violation
from tests.diagnostics.render.conftest import LAYER_VIOLATION, read_line

BRIEF = RuleBrief(
    code="MOD013",
    name="modeling/prefer-error-name",
    status="error · off in smelt.yml",
    instruction="Name exception classes with an Error suffix and their module errors",
    smell="An exception class ends in Exception.",
    no_smell="A published name that outside code catches.",
    enable="rules: {modeling/prefer-error-name: error}",
)


def _finding(
    path: str, line: int | None, name: str, *, kind: str = "class"
) -> Violation:
    suggested = "errors" if kind == "module" else name.replace("Exception", "Error")
    return Violation(
        code="MOD013",
        rule="modeling/prefer-error-name",
        severity=Severity.ERROR,
        message=f'{kind} "{name}" is named with Exception',
        path=path,
        line=line,
        column=1 if line else None,
        expected={"kind": kind, "name": name, "suggested_name": suggested},
        hint=f'Rename it to "{suggested}".',
        category=Category.MODELING,
    )


def _report(*violations: Violation) -> Report:
    return Report(
        violations=list(violations), modules=3, categories=[Category.MODELING]
    )


class TestBrief:
    def test_states_the_rule_once_above_its_findings(self) -> None:
        report = _report(_finding("app/exceptions.py", 4, "ParseException"))

        out = render_rule_view(report, BRIEF, read_line)

        assert out.splitlines()[:4] == [
            "MOD013 modeling/prefer-error-name · error · off in smelt.yml",
            "Name exception classes with an Error suffix and their module errors",
            "  Smell:    An exception class ends in Exception.",
            "  No smell: A published name that outside code catches.",
        ]

    def test_advice_every_finding_repeats_is_the_fix(self) -> None:
        shared = [
            replace(_finding(path, 1, "ParseException"), hint="Rename  the\nclass.")
            for path in ("app/a.py", "app/b.py")
        ]

        out = render_rule_view(_report(*shared), BRIEF, read_line)

        assert out.count("Rename the class.") == 1
        assert "  Fix:      Rename the class." in out

    def test_the_rule_repair_replaces_the_advice_of_each_finding(self) -> None:
        report = _report(
            _finding("app/a.py", 1, "ParseException"),
            _finding("app/b.py", 1, "LoadException"),
        )
        brief = replace(BRIEF, repair="Rename to the suggested name.")

        out = render_rule_view(report, brief, read_line)

        assert "  Fix:      Rename to the suggested name." in out
        assert "Rename it to" not in out


class TestListing:
    def test_findings_without_a_subject_show_their_message_and_own_advice(self) -> None:
        report = _report(
            _finding("app/exceptions.py", 9, "LoadException"),
            _finding("app/exceptions.py", 4, "ParseException"),
        )

        out = render_rule_view(report, BRIEF, read_line)

        assert out.splitlines()[5:10] == [
            "2 findings · 1 file",
            "",
            "app/exceptions.py",
            '  4  class "ParseException" is named with Exception -> Rename it to "ParseError".',
            '  9  class "LoadException" is named with Exception -> Rename it to "LoadError".',
        ]

    def test_subjects_share_lines_under_their_file(self) -> None:
        report = _report(
            _finding("app/exceptions.py", 4, "ParseException"),
            _finding("app/exceptions.py", 9, "LoadException"),
            _finding("app/other.py", 2, "SaveException"),
        )
        brief = replace(
            BRIEF, subject="{name} -> {suggested_name}", repair="Rename the class."
        )

        out = render_rule_view(report, brief, read_line)

        assert out.splitlines()[6:11] == [
            "3 findings · 2 files",
            "",
            "app/exceptions.py",
            "  4 ParseException -> ParseError · 9 LoadException -> LoadError",
            "",
        ]
        assert "app/other.py  2 SaveException -> SaveError" in out

    def test_long_subjects_wrap_between_findings(self) -> None:
        names = [f"Step{number}WithALongOperationNameException" for number in range(3)]
        report = _report(
            *(
                _finding("app/exceptions.py", line, name)
                for line, name in enumerate(names, start=1)
            )
        )
        brief = replace(BRIEF, subject="{name}", repair="Rename the class.")

        out = render_rule_view(report, brief, read_line)

        assert (
            "  1 Step0WithALongOperationNameException · 2 Step1WithALongOperationNameException\n"
            "  3 Step2WithALongOperationNameException\n"
        ) in out

    def test_a_missing_subject_key_falls_back_to_the_message(self) -> None:
        report = _report(_finding("app/exceptions.py", 4, "ParseException"))
        brief = replace(BRIEF, subject="{name} in {owner}", repair="Rename the class.")

        out = render_rule_view(report, brief, read_line)

        assert (
            'app/exceptions.py  4  class "ParseException" is named with Exception'
            in out
        )

    def test_groups_follow_the_decision_key_largest_first(self) -> None:
        report = _report(
            _finding("app/exceptions.py", None, "exceptions", kind="module"),
            _finding("app/exceptions.py", 4, "ParseException"),
            _finding("app/exceptions.py", 9, "LoadException"),
        )
        brief = replace(
            BRIEF, group_by=("owner", "kind"), subject="{name} -> {suggested_name}"
        )

        out = render_rule_view(report, brief, read_line)

        assert out.splitlines()[5:13] == [
            "3 findings · 1 file · 2 groups",
            "",
            "Kind: class (2)",
            "  app/exceptions.py",
            '    4  ParseException -> ParseError -> Rename it to "ParseError".',
            '    9  LoadException -> LoadError -> Rename it to "LoadError".',
            "",
            "Kind: module (1)",
        ]
        assert (
            '  app/exceptions.py  exceptions -> errors -> Rename it to "errors".' in out
        )

    def test_a_group_states_the_advice_its_findings_share(self) -> None:
        modules = [
            _finding(path, None, "exceptions", kind="module")
            for path in ("app/a/exceptions.py", "app/b/exceptions.py")
        ]
        report = _report(*modules, _finding("app/a/exceptions.py", 4, "ParseException"))
        brief = replace(BRIEF, group_by=("kind",), subject="{name}")

        out = render_rule_view(report, brief, read_line)

        assert out.splitlines()[7:11] == [
            "Kind: module (2)",
            '  Fix:      Rename it to "errors".',
            "  app/a/exceptions.py  exceptions",
            "  app/b/exceptions.py  exceptions",
        ]


class TestClosing:
    def test_open_findings_name_the_entry_that_enables_the_rule(self) -> None:
        report = _report(_finding("app/exceptions.py", 4, "ParseException"))

        out = render_rule_view(report, BRIEF, read_line)

        assert out.splitlines()[-2:] == [
            "Done when this command reports no findings. Then enable it: rules: {modeling/prefer-error-name: error}",
            "Details: smelt explain MOD013 · evidence per finding: --verbose",
        ]

    def test_a_clean_rule_without_its_policy_names_the_requirement(self) -> None:
        brief = replace(BRIEF, enable="", requires="architecture.layers is declared.")

        out = render_rule_view(_report(), brief, read_line)

        assert out.splitlines()[-5:] == [
            "0 findings",
            "",
            "No MOD013 findings.",
            "Requires: architecture.layers is declared.",
            "Details: smelt explain MOD013 · evidence per finding: --verbose",
        ]

    def test_counts_name_hidden_debt_scope_and_comparison(self) -> None:
        report = replace(
            _report(_finding("app/exceptions.py", 4, "ParseException")),
            in_debt=3,
            suppressed=1,
            scope=("app",),
            comparison={"mode": "changed"},
        )

        out = render_rule_view(report, BRIEF, read_line)

        assert (
            "1 finding · 1 file · 3 in debt (--no-debt lists them) · 1 suppressed"
            in out
        )
        assert "Scope: app\nScope: findings new since the compared commit.\n" in out

    def test_findings_of_other_rules_keep_their_detail(self) -> None:
        report = replace(
            _report(
                _finding("app/exceptions.py", 4, "ParseException"), LAYER_VIOLATION
            ),
            configuration_diagnostics=[
                ConfigurationDiagnostic(
                    path="smelt.yml",
                    key="rules.x",
                    message="unused",
                    severity=Severity.WARNING,
                )
            ],
        )

        out = render_rule_view(report, BRIEF, read_line)

        _, _, rest = out.partition("Other findings in this run:\n")
        assert rest.startswith("warning: smelt.yml: rules.x: unused\n")
        assert "ARC001 architecture/layer-boundary  [error]" in rest
        assert "MOD013" not in rest

from smelt.diagnostics.suppressions import parse_suppressions


class TestParseSuppressions:
    def test_codes_and_reason(self) -> None:
        source = "import x  # smelt: ignore[arc001, VIS] -- legacy wiring\n"

        [found] = parse_suppressions("a.py", source)

        assert (found.line, found.codes, found.reason, found.file_level) == (
            1,
            ("ARC001", "VIS"),
            "legacy wiring",
            False,
        )
        assert found.column == source.index("#")

    def test_rule_names_keep_their_spelling(self) -> None:
        source = "x = 1  # smelt: ignore[modeling/Field-Mutation, smt3] -- y\n"

        [found] = parse_suppressions("a.py", source)

        assert found.codes == ("modeling/Field-Mutation", "SMT3")

    def test_resolved_names_keep_their_written_label(self) -> None:
        source = "x = 1  # smelt: ignore[modeling/dataclass-field-mutation] -- y\n"
        [parsed] = parse_suppressions("a.py", source)

        found = parsed.resolve_names({"modeling/dataclass-field-mutation": "MOD005"})

        assert found.covers("MOD005") is True
        assert found.label("MOD005") == "modeling/dataclass-field-mutation"

    def test_file_level_without_codes(self) -> None:
        [found] = parse_suppressions("a.py", "# smelt: ignore-file -- generated\n")

        assert found.file_level is True
        assert found.codes == ()
        assert found.covers("SMT999") is True

    def test_marker_inside_string_is_ignored(self) -> None:
        source = 'text = "# smelt: ignore[ARC001] -- no"\n'

        assert parse_suppressions("a.py", source) == []

    def test_unused_codes_by_prefix(self) -> None:
        [found] = parse_suppressions("a.py", "x = 1  # smelt: ignore[ARC, TST]\n")

        assert found.unused_codes(["ARC004"]) == ("TST",)

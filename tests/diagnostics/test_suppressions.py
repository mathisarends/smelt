from smelt.diagnostics.suppressions import parse_suppressions


class TestParseSuppressions:
    def test_codes_and_reason(self) -> None:
        source = "import x  # smelt: ignore[smt101, SMT2] -- legacy wiring\n"

        [found] = parse_suppressions("a.py", source)

        assert (found.line, found.codes, found.reason, found.file_level) == (
            1,
            ("SMT101", "SMT2"),
            "legacy wiring",
            False,
        )
        assert found.column == source.index("#")

    def test_file_level_without_codes(self) -> None:
        [found] = parse_suppressions("a.py", "# smelt: ignore-file -- generated\n")

        assert found.file_level is True
        assert found.codes == ()
        assert found.covers("SMT999") is True

    def test_marker_inside_string_is_ignored(self) -> None:
        source = 'text = "# smelt: ignore[SMT101] -- no"\n'

        assert parse_suppressions("a.py", source) == []

    def test_unused_codes_by_prefix(self) -> None:
        [found] = parse_suppressions("a.py", "x = 1  # smelt: ignore[SMT1, SMT4]\n")

        found.mark_used("SMT104")

        assert found.unused_codes() == ("SMT4",)

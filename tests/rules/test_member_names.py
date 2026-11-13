from __future__ import annotations

from smelt.rules.member_names import private_name, rename_advice


class TestRenameAdvice:
    def test_usual_private_name_is_not_repeated(self) -> None:
        assert rename_advice("hits", private_name("hits", frozenset())) == (
            "add a leading underscore"
        )

    def test_collision_names_the_suggested_target(self) -> None:
        suggested = private_name("hits", frozenset({"_hits"}))

        assert rename_advice("hits", suggested) == (
            "rename to _hits_internal (the underscored name is taken)"
        )

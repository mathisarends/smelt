from __future__ import annotations

from smelt.diagnostics.groups import group_findings
from smelt.diagnostics.violation import ImportLink, Severity, Violation


def _import(
    path: str,
    edge: str,
    chain: tuple[ImportLink, ...] = (),
    code: str = "SMT101",
) -> Violation:
    return Violation(
        code=code,
        rule="layer-boundary",
        severity=Severity.ERROR,
        message=f"{edge} in {path}",
        path=path,
        line=1,
        source_module=path.removesuffix(".py").replace("/", "."),
        target_module="app.llm.providers.openai",
        import_chain=chain,
        edge=edge,
    )


def _through_facade(importer: str) -> tuple[ImportLink, ...]:
    return (
        ImportLink(importer, "app.llm", 1),
        ImportLink("app.llm", "app.llm.providers.openai", 3),
    )


class TestGroupFindings:
    def test_facade_groups_every_finding_it_relays(self) -> None:
        found = [
            _import(
                "app/chat/a.py",
                "chat.application -> infrastructure",
                _through_facade("app.chat.a"),
            ),
            _import(
                "app/chat/b.py",
                "chat.application -> infrastructure",
                _through_facade("app.chat.b"),
            ),
            _import(
                "app/user/c.py",
                "user.domain -> infrastructure",
                _through_facade("app.user.c"),
                code="SMT102",
            ),
            _import("app/user/d.py", "user.domain -> infrastructure"),
        ]

        groups = group_findings(found)

        facade = next(g for g in groups if g["kind"] == "facade")
        assert facade == {
            "kind": "facade",
            "key": "app.llm",
            "count": 3,
            "codes": ["SMT101", "SMT102"],
            "violations": [v.finding_id for v in found[:3]],
            "direct": 0,
            "transitive": 3,
            "edges": [
                "chat.application -> infrastructure",
                "user.domain -> infrastructure",
            ],
        }
        edges = {g["key"]: g for g in groups if g["kind"] == "edge"}
        assert edges["SMT101 chat.application -> infrastructure"]["count"] == 2
        mixed = edges.get("SMT101 user.domain -> infrastructure")
        assert mixed is None  # one SMT101 and one SMT102: two decisions

    def test_edge_groups_count_direct_and_transitive(self) -> None:
        found = [
            _import("app/a.py", "a -> b"),
            _import("app/c.py", "a -> b", _through_facade("app.c")),
        ]

        [edge] = [g for g in group_findings(found) if g["kind"] == "edge"]

        assert (edge["direct"], edge["transitive"]) == (1, 1)

    def test_cycle_keeps_its_witness_path(self) -> None:
        chain = (
            ImportLink("app.features.x", "app.platform.auth", 6),
            ImportLink("app.platform.auth.guard", "app.features.y", 8),
        )
        cycle = Violation(
            code="SMT104",
            rule="import-cycle",
            severity=Severity.ERROR,
            message="import cycle",
            path="app/features/x.py",
            import_chain=chain,
            expected={"cycle": ["features", "platform"], "type_checking_only": True},
            edge="features -> platform -> features",
        )

        [group] = group_findings([cycle])

        assert group["kind"] == "cycle"
        assert group["witness"] == [link.to_json() for link in chain]
        assert group["type_checking_only"] is True

    def test_test_moves_group_by_directories(self) -> None:
        def move(name: str) -> Violation:
            return Violation(
                code="SMT401",
                rule="test-location",
                severity=Severity.ERROR,
                message=f"{name} belongs in tests/auth/infrastructure/",
                path=f"tests/auth/{name}",
                expected={"path": f"tests/auth/infrastructure/{name}"},
            )

        [group] = group_findings([move("test_a.py"), move("test_b.py")])

        assert group["key"] == "tests/auth/ -> tests/auth/infrastructure/"
        assert group["count"] == 2

    def test_order_is_deterministic(self) -> None:
        found = [_import(f"app/m{n}.py", f"e{n % 2} -> x") for n in range(4)]

        keys = [(g["kind"], g["key"]) for g in group_findings(found)]
        reversed_keys = [
            (g["kind"], g["key"]) for g in group_findings(list(reversed(found)))
        ]

        assert (
            keys
            == reversed_keys
            == [("edge", "SMT101 e0 -> x"), ("edge", "SMT101 e1 -> x")]
        )

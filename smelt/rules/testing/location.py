from __future__ import annotations

import difflib
import posixpath
import re
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING

from smelt.analysis.context import AnalysisContext, Index
from smelt.analysis.exports import resolve_dotted
from smelt.config.patterns import path_matches
from smelt.diagnostics.violation import Category, Severity, Violation
from smelt.model import is_within
from smelt.rules.base import BaseRule, RuleDoc
from smelt.rules.common import display_module_path

if TYPE_CHECKING:
    from collections.abc import Iterator

    from smelt.analysis.files import TestFile
    from smelt.analysis.syntax import ModuleSyntax


def _first_party_module(ctx: AnalysisContext, qualname: str) -> str | None:
    """The longest source module that prefixes ``qualname``."""
    parts = qualname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in ctx.files.sources:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class _Import:
    """A first-party name a test imports and the module that defines it."""

    name: str  # ``ChannelCommands``, or the module for ``import``/``from . import x``
    module: str
    via: str | None = None  # the package facade that re-exports it


def _resolved_imports(ctx: AnalysisContext, syntax: ModuleSyntax) -> list[_Import]:
    """First-party imports, followed through package facades to their modules.

    ``from app.channels.application import ChannelCommands`` names the package, but
    the test exercises ``app/channels/application/commands/registry.py``.
    """
    found: list[_Import] = []
    for target in syntax.bindings.values():
        module = _first_party_module(ctx, target)
        if module is None:
            continue
        item = _Import(target, module)
        if target != module:
            name = target[len(module) + 1 :]
            item = _Import(name, module)
            resolved = resolve_dotted(target, ctx.syntax.namespace)
            if resolved is not None and resolved[0] != module:
                item = _Import(name, resolved[0], via=module)
        if item not in found:
            found.append(item)
    return found


def _modules(imports: list[_Import]) -> list[str]:
    return list(dict.fromkeys(item.module for item in imports))


def _subject(name: str) -> str:
    stem = name.removesuffix(".py")
    return stem.removeprefix("test_").removesuffix("_test")


_PLACEHOLDER = re.compile(r"(\{path\}/|\{path\}|\{module\}|\{root\})")
_GROUPS = {
    "{path}/": r"(?:(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)/)?",
    "{path}": r"(?P<path>[A-Za-z_]\w*(?:/[A-Za-z_]\w*)*)?",
    "{module}": r"(?P<module>[A-Za-z_]\w*)",
    "{root}": r"(?P<root>[A-Za-z_]\w*)",
}


@cache
def _mirror_regex(pattern: str) -> re.Pattern[str]:
    parts = _PLACEHOLDER.split(pattern)
    return re.compile("".join(_GROUPS.get(part, re.escape(part)) for part in parts))


@dataclass(frozen=True, slots=True)
class _Mirror:
    """What a test path says it mirrors: ``billing/test_invoice.py`` -> billing, invoice."""

    directories: list[str]
    module: str
    root: str | None


def _mirror_parts(ctx: AnalysisContext, test: TestFile) -> _Mirror | None:
    relative = test.path.removeprefix(f"{test.test_root}/")
    match = _mirror_regex(ctx.config.tests.mirror).fullmatch(relative)
    if match is None:
        return None
    path = match.group("path") if "path" in match.groupdict() else None
    root = match.group("root") if "root" in match.groupdict() else None
    return _Mirror(path.split("/") if path else [], match.group("module"), root)


def _roots(ctx: AnalysisContext, mirror: _Mirror, test_root: str) -> list[str]:
    roots = member_roots(ctx, test_root)
    if mirror.root is None:
        return roots
    return [mirror.root] if mirror.root in roots else []


def member_roots(ctx: AnalysisContext, test_root: str) -> list[str]:
    """The root packages a test root belongs to.

    In a workspace, ``libs/agent/tests`` tests the package under ``libs/agent/src``:
    the source roots sharing the longest leading path with the test root win.
    """
    roots = ctx.config.project.root_packages
    shared = {root: _common_depth(ctx, root, test_root) for root in roots}
    best = max(shared.values(), default=0)
    if best == 0:
        return roots
    return [root for root in roots if shared[root] == best]


def _common_depth(ctx: AnalysisContext, root: str, test_root: str) -> int:
    package = ctx.files.packages.get(root)
    if package is None:
        return 0
    depth = 0
    for left, right in zip(
        package.source_root.strip("/").split("/"), test_root.split("/"), strict=False
    ):
        if left != right or left in ("", "."):
            break
        depth += 1
    return depth


def _subjects(subject: str, *, suffixes: bool) -> list[str]:
    """The module names a test may cover: ``invoice_rounding`` -> also ``invoice``."""
    if not suffixes:
        return [subject]
    parts = subject.split("_")
    return ["_".join(parts[:end]) for end in range(len(parts), 0, -1)]


def _mirrors_source(ctx: AnalysisContext, mirror: _Mirror, test_root: str) -> bool:
    files = ctx.files
    for root in _roots(ctx, mirror, test_root):
        package = ".".join([root, *mirror.directories])
        package_name = mirror.directories[-1] if mirror.directories else root
        for name in _subjects(mirror.module, suffixes=ctx.config.tests.mirror_suffixes):
            source = files.sources.get(f"{package}.{name}")
            if source is not None and not source.is_package:
                return True
            if name == package_name and package in files.packages:
                return True
    return False


def _missing_module(ctx: AnalysisContext, mirror: _Mirror, test_root: str) -> str:
    root = (_roots(ctx, mirror, test_root) or member_roots(ctx, test_root))[0]
    return ".".join([root, *mirror.directories, mirror.module])


def _similar_module(ctx: AnalysisContext, module: str, imported: list[str]) -> str:
    """`` (did you mean "fernet_token_cypher.py"?)`` for a typo next to a real module.

    Names are compared without ``.py``, which made ``memory.py`` look like
    ``errors.py``. Only a module the test imports can be the one it misspells: a
    similar file name alone says nothing about what the test covers.
    """
    package, name = module.rsplit(".", 1)
    siblings = [
        source.module
        for source in ctx.files.sources.values()
        if not source.is_package
        and source.module.rsplit(".", 1)[0] == package
        and source.module in imported
    ]
    stems = [sibling.rsplit(".", 1)[1] for sibling in siblings]
    # Typos score above 0.85 (invoise/invoice); spotify_service/spotify_search
    # scores 0.83 and is another module, not a misspelling.
    match = difflib.get_close_matches(name, stems, n=1, cutoff=0.85)
    return f' (did you mean "{match[0]}.py"?)' if match else ""


def _named_package(
    ctx: AnalysisContext, mirror: _Mirror, test_root: str, imported: list[str]
) -> str | None:
    """The package the test is named after, next to the mirrored path.

    ``application/test_commands.py`` covers ``application/commands/``, unless it
    imports first-party code and none of it from that package.
    """
    found = [
        package
        for root in _roots(ctx, mirror, test_root)
        if (package := ".".join([root, *mirror.directories, mirror.module]))
        in ctx.files.packages
    ]
    if len(found) != 1:
        return None
    if imported and not any(is_within(m, found[0]) for m in imported):
        return None
    return found[0]


def _suffix_hint(ctx: AnalysisContext) -> str:
    if ctx.config.tests.mirror_suffixes:
        return " Topic tests may add a suffix: test_<module>_<topic>.py."
    return " tests.mirror_suffixes allows topic tests like test_<module>_<topic>.py."


def _suffix_sibling(
    ctx: AnalysisContext, mirror: _Mirror, test_root: str, imported: list[str]
) -> str | None:
    """The module next to the mirrored path that the name ends with.

    ``health/presentation/test_health_presentation_router.py`` mirrors
    ``health/presentation/router.py`` once the prefix is dropped. Not when the test
    imports another module of that package: ``test_..._event_mapper.py`` importing
    ``presentation.rpc.mappers`` is not about ``presentation/mapper.py``.
    """
    found = [
        source.module
        for root in _roots(ctx, mirror, test_root)
        for source in ctx.files.sources.values()
        if not source.is_package
        and source.module.rsplit(".", 1)[0] == ".".join([root, *mirror.directories])
        and _spells_package_path(mirror.module, source.module)
    ]
    if len(found) != 1:
        return None
    package = found[0].rsplit(".", 1)[0]
    others = [
        m for m in imported if is_within(m, package) and m not in (package, found[0])
    ]
    return None if others else found[0]


def _root_pattern_hint(ctx: AnalysisContext, mirror: _Mirror, test_root: str) -> str:
    """A hint when the test path starts with the root package the pattern leaves out."""
    pattern = ctx.config.tests.mirror
    if "{root}" in pattern or not mirror.directories:
        return ""
    root, *directories = mirror.directories
    if root not in member_roots(ctx, test_root):
        return ""
    if not _mirrors_source(ctx, _Mirror(directories, mirror.module, root), test_root):
        return ""
    return (
        f' The test path starts with the root package "{root}"; if all tests do, '
        f'set tests.mirror to "{{root}}/{pattern}".'
    )


def _mirror_target(ctx: AnalysisContext, test_root: str, module: str) -> str:
    """Where tests.mirror puts the test of ``module`` (a module, not a package)."""
    root, *directories, name = module.split(".")
    path = "/".join(directories)
    relative = (
        ctx.config.tests.mirror.replace("{path}/", f"{path}/" if path else "")
        .replace("{path}", path)
        .replace("{module}", name)
        .replace("{root}", root)
    )
    return f"{test_root}/{relative}"


def mirror_path(ctx: AnalysisContext, module: str) -> str | None:
    """The mirrored test path of ``module`` under the test root of its package.

    Segments may be placeholders such as ``<layer>``; they are kept as written.
    """
    root = module.split(".", 1)[0]
    for test_root in ctx.config.project.test_roots:
        if root in member_roots(ctx, test_root.strip("/")):
            return _mirror_target(ctx, test_root.strip("/"), module)
    return None


class MisplacedTestFile(BaseRule):
    code = "SMT401"
    name = "test-location"
    category = Category.TESTS
    default_severity = Severity.ERROR
    requires = frozenset({Index.FILES, Index.SYNTAX})
    doc = RuleDoc(
        summary="A test file does not live where tests.layout expects it.",
        rationale=(
            "A predictable test location lets readers and agents find the tests of a "
            "module, and a test whose mirrored source no longer exists is left over from "
            "a rename or move. With `layout: mirror` the path decides: "
            "tests/billing/test_invoice.py needs app/billing/invoice.py, a package test "
            "tests/billing/test_billing.py needs app/billing/. Not every module needs a "
            "test. tests.mirror sets the convention, relative to the test root."
        ),
        bad="tests/test_invoice.py            # layout: mirror, source app/billing/invoice.py",
        good="tests/billing/test_invoice.py",
        fix=(
            "Move or rename the file so its path mirrors the module it tests, merge it "
            "into that module's test, or list deliberately unmirrored tests "
            "(behaviour, integration, e2e) in tests.unmirrored. A missing mirrored "
            "source says nothing about whether the tested behaviour exists. Imports "
            "through package facades count as imports of the defining module. A move "
            "is suggested only when the test imports its subject (or is named exactly "
            "after it, marked `evidence: name`); otherwise `expected.candidates` lists "
            "the imported modules with their names and mirrored test paths to choose "
            "from, and two imported modules of the test's name stay undecided."
        ),
        config=(
            "tests.layout",
            "tests.mirror",
            "tests.unmirrored",
            "tests.mirror_suffixes",
            "project.test_roots",
        ),
    )

    def check(self, ctx: AnalysisContext) -> Iterator[Violation]:
        tests = ctx.config.tests
        if tests.layout == "none":
            return
        for path, test in sorted(ctx.files.tests.items()):
            if test.is_conftest:
                continue
            if not any(path_matches(p, path) for p in tests.unmirrored):
                yield from self._check_mirror(ctx, test)

    def _check_mirror(
        self, ctx: AnalysisContext, test: TestFile
    ) -> Iterator[Violation]:
        mirror = _mirror_parts(ctx, test)
        if mirror is not None and _mirrors_source(ctx, mirror, test.test_root):
            return
        syntax = ctx.syntax.for_path(test.path)
        imports = _resolved_imports(ctx, syntax) if syntax is not None else []
        modules = _modules(imports)
        subject = mirror.module if mirror else _subject(test.name)
        named = _named_modules(ctx, subject, modules)
        tested = named[0] if len(named) == 1 else None
        if not named and mirror is not None:
            tested = _suffix_sibling(ctx, mirror, test.test_root, modules)
        package = None
        if not named and tested is None and mirror is not None:
            package = _named_package(ctx, mirror, test.test_root, modules)
        covered = tested or package
        taken = ""
        if covered is not None:
            # A package's own test sits inside it: billing/stripe/test_stripe.py
            target = covered if tested else f"{covered}.{subject}"
            expected = _mirror_target(ctx, test.test_root, target)
            if expected in ctx.files.tests and expected != test.path:
                taken = f" {expected} already exists; merge the two tests."
            elif expected != test.path:
                yield self._misplaced(ctx, test, covered, expected, modules)
                return
        if mirror is None:
            pattern = ctx.config.tests.mirror
            yield self.violation(
                f"{test.name} does not match tests.mirror ({pattern})",
                path=test.path,
                expected={"pattern": f"{test.test_root}/{pattern}"},
                hint=_GENERIC_HINT + _suffix_hint(ctx) + taken,
            )
            return
        module = _missing_module(ctx, mirror, test.test_root)
        source = ctx.files.module_to_path(module)
        info = ctx.model.info(module)
        ranked = _ranked(ctx, test.test_root, module, named or modules)
        candidates = _candidates(ctx, test.test_root, ranked, imports)
        details: dict[str, object] = {"source": source}
        message = f"{test.name} has no source module at its mirrored path {source}"
        if len(named) <= 1:
            message += _similar_module(ctx, module, modules)
        if len(named) > 1:
            message += f"; it imports {len(named)} modules named {subject}"
            hint = (
                f"It imports {_listing(ctx, ranked)}. Move it next to the one it tests, "
                "split it, or list it in tests.unmirrored if it tests how they work "
                "together."
            )
            details |= {"subject": "ambiguous", "candidates": candidates}
        elif candidates:
            hint = (
                f"{_NO_SOURCE} It imports {_listing(ctx, ranked)}: rename or move it "
                "to the test of the module it covers, merge it into that test, or list "
                "it in tests.unmirrored if it tests behaviour across them."
            )
            details |= {"subject": "unknown", "candidates": candidates}
        else:
            hint = _GENERIC_HINT
        yield self.violation(
            message,
            path=test.path,
            feature=info.feature if info else None,
            layer=info.layer if info else None,
            expected=details,
            hint=hint
            + _suffix_hint(ctx)
            + taken
            + _root_pattern_hint(ctx, mirror, test.test_root),
        )

    def _misplaced(
        self,
        ctx: AnalysisContext,
        test: TestFile,
        module: str,
        expected: str,
        imported: list[str],
    ) -> Violation:
        directory = posixpath.dirname(expected)
        if directory == posixpath.dirname(test.path):
            message = f"{test.name} should be named {posixpath.basename(expected)}"
        else:
            message = f"{test.name} belongs in {directory}/"
        info = ctx.model.info(module)
        package = module in ctx.files.packages
        source = ctx.files.module_to_path(module, package=package)
        hint = f"Move the file to {expected}."
        evidence = "imports"
        if not any(is_within(m, module) for m in imported):
            evidence = "name"
            hint = (
                f"Move the file to {expected}. Only its name points there: it imports "
                f"no code of {source}, so check that it really tests it."
            )
        return self.violation(
            message,
            path=test.path,
            feature=info.feature if info else None,
            layer=info.layer if info else None,
            expected={
                "path": expected,
                "source": source,
                "subject": "package" if package else "module",
                "evidence": evidence,
            },
            hint=hint,
        )


def _named_modules(ctx: AnalysisContext, subject: str, modules: list[str]) -> list[str]:
    """The imported modules the test is named after; several make it ambiguous."""
    plain = [m for m in modules if m not in ctx.files.packages]
    exact = [m for m in plain if m.rsplit(".", 1)[-1] == subject]
    if exact:
        return exact
    # test_session_infrastructure_repository.py spells out the package path
    suffixed = [m for m in plain if _spells_package_path(subject, m)]
    return suffixed if len(suffixed) == 1 else []


def _spells_package_path(subject: str, module: str) -> bool:
    """``session_infrastructure_repository`` names ``session.infrastructure.repository``.

    The prefix must be the end of the module's package path: a topic such as
    ``agent_workspace_update_events`` does not make a test about ``agent/events.py``.
    """
    *package, name = module.split(".")
    if not subject.endswith(f"_{name}"):
        return False
    prefix = subject[: -len(name) - 1]
    return any(prefix == "_".join(package[start:]) for start in range(len(package)))


_NO_SOURCE = (
    "A path without a source module does not mean the tested behaviour is missing."
)
_GENERIC_HINT = (
    f"{_NO_SOURCE} Move or rename the test to mirror the module it covers, merge it "
    "into that module's test, delete it if the module is gone, or list a deliberate "
    "behaviour or integration test in tests.unmirrored."
)
# Enough to choose from; the JSON says how many there are.
_MAX_CANDIDATES = 5


def _ranked(
    ctx: AnalysisContext, test_root: str, missing: str, modules: list[str]
) -> list[str]:
    """Imported modules of the test's own member, nearest to its mirrored path first.

    Within the mirrored package, names closer to the test's name come first; that
    orders the evidence, it adds no module the test does not import.
    """
    roots = member_roots(ctx, test_root)
    package, name = missing.rsplit(".", 1)
    return sorted(
        (m for m in modules if m.split(".")[0] in roots),
        key=lambda m: (
            not is_within(m, package),
            -difflib.SequenceMatcher(None, name, m.rsplit(".", 1)[-1]).ratio(),
            m,
        ),
    )


def _candidates(
    ctx: AnalysisContext, test_root: str, ranked: list[str], imports: list[_Import]
) -> list[dict[str, object]]:
    """The first ranked modules with what the test imports from them; none is a move."""
    found: list[dict[str, object]] = []
    for module in ranked[:_MAX_CANDIDATES]:
        is_package = module in ctx.files.packages
        target = f"{module}.{module.rsplit('.', 1)[-1]}" if is_package else module
        found.append(
            {
                "module": module,
                "kind": "package" if is_package else "module",
                "source": ctx.files.module_to_path(module, package=is_package),
                "test_path": _mirror_target(ctx, test_root, target),
                "imports": sorted({i.name for i in imports if i.module == module}),
                "via": sorted({i.via for i in imports if i.module == module and i.via}),
            }
        )
    return found


def _listing(ctx: AnalysisContext, modules: list[str]) -> str:
    shown = [
        display_module_path(ctx.model, m, package=m in ctx.files.packages)
        for m in modules[:3]
    ]
    more = len(modules) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more else "")

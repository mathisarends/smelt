"""Compare the Rust config loader with the Python loader, case by case.

Each case is a config file in a small temporary project: valid policies, every key
replaced by values of the wrong type, removed and unknown keys, YAML scalars and
YAML structures. Both loaders must produce the same policy or the same error.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from smelt.config.errors import ConfigError
from smelt.config.loader import load_config
from smelt.config.models import RuleOverride

if TYPE_CHECKING:
    from collections.abc import Iterator

_WRONG_VALUES: tuple[Any, ...] = (
    None,
    True,
    False,
    0,
    1,
    -1,
    2,
    3,
    1.5,
    "",
    " ",
    "x",
    "a.b",
    "allow",
    "2",
    "yes",
    [],
    ["x"],
    [1],
    [None],
    {},
    {"x": 1},
    {"value": 1},
    {"reason": "r"},
    {"value": "x", "reason": "r"},
    {1: "x"},
)
_UNKNOWN_KEYS = (
    "unknown_key",
    "structure",
    "modules",
    "imports",
    "contracts",
    "coverage",
    "enabled",
    "preset",
    "export_at",
    "max_module_imports",
    "member_visibility",
)
_SCALARS = (
    "yes",
    "no",
    "on",
    "off",
    "y",
    "n",
    "true",
    "True",
    "TRUE",
    "false",
    "null",
    "Null",
    "~",
    "0",
    "007",
    "0o7",
    "0x1F",
    "0b101",
    "1_000",
    "+1",
    "-1",
    "1.5",
    "1.",
    ".5",
    "-.5",
    "1e3",
    "1.0e+3",
    "1_0.5",
    ".inf",
    "-.INF",
    ".nan",
    "12:30",
    "12:30:15",
    "1:20.5",
    "2020-01-01",
    "2020-1-1 10:00:00",
    '"quoted"',
    "'single'",
    "'it''s'",
    '"tab\\there"',
    "!!str 1",
    "!!int '2'",
    "!!bool 'yes'",
    "!!float 1",
    "!!null ''",
    "!!python/name:os.system",
    "[1, 2]",
    "{a: 1}",
    "|\n    text",
    ">\n    folded",
)
_STRUCTURES = (
    "",
    "# only a comment\n",
    "---\n",
    "---\nversion: 2\n---\nversion: 2\n",
    "- a\n- b\n",
    "plain\n",
    "version: 2\nversion: 2\n",
    "version: 2\nproject: &p {root_packages: [app]}\nstyle: *p\n",
    "base: &b {root_packages: [app]}\nversion: 2\nproject:\n  <<: *b\n",
    "version: 2\nproject:\n  <<: {root_packages: [app]}\n  root_packages: [other]\n",
    "version: 2\nproject:\n  <<: [{root_packages: [app]}, {source_roots: [.]}]\n",
    "version: 2\nproject:\n  <<: 1\n",
    "version: 2\nproject: &p\n  self: *p\n",
    "version: 2\nproject: {root_packages: [app]}\nrules: &r [*r]\n",
    "version: 2\nproject: *missing\n",
    "version: 2\nproject: {root_packages: [app]\n",
    "version: 2\n  project: {}\n",
    "? [a]\n: 1\n",
    "1: a\n1.0: b\n",
    "true: a\n1: b\n",
    "version: 2\nproject: {root_packages: [app]}\n1: x\n",
    "version: 2\nproject: {root_packages: [app]}\n-1: x\n",
    "version: 2\nproject: {root_packages: [app]}\nrules: {1: off}\n",
    "version: 2\nproject: {root_packages: [app]}\nrules: {null: off}\n",
    "\ufeffversion: 2\nproject: {root_packages: [app]}\n",
    "version: 2\r\nproject: {root_packages: [app]}\r\n",
    "version: 1\n",
    "version: 2.0\nproject: {root_packages: [app]}\n",
    "version: '2'\nproject: {root_packages: [app]}\n",
    "version: true\nproject: {root_packages: [app]}\n",
)
_SINGLE: dict[str, Any] = {
    "version": 2,
    "project": {
        "root_packages": ["app"],
        "source_roots": ["."],
        "test_roots": ["tests"],
        "exclude": ["app/generated", {"path": "app/vendor", "reason": "vendored"}],
        "python_version": "3.12",
    },
    "architecture": {
        "features": {"root": "app.features"},
        "shared": ["app.shared"],
        "composition_root": ["app.main"],
        "wiring": ["app.*.wiring"],
        "layers": {
            "domain": {
                "path": "domain",
                "third_party": {"default": "deny", "allow": ["pydantic"]},
            },
            "application": {"path": "application", "may_depend_on": ["domain"]},
            "infrastructure": {
                "modules": ["app.infra"],
                "may_depend_on": ["application", "domain"],
                "third_party": "allow",
            },
        },
        "cross_feature": {
            "default": "deny",
            "allow": [
                "application -> domain",
                {"from": "a.application", "to": "b.domain"},
            ],
        },
        "import_graph": {
            "type_checking": "ignore",
            "transitive": True,
            "cycles": ["features", "modules"],
            "type_checking_cycles": "ignore",
        },
        "packages": {
            "api": {
                "match": "app.features.*",
                "required": ["service"],
                "public": ["service", "."],
                "min_imports_to_bundle": 2,
                "min_importers_to_export": 2,
                "count_tests": True,
                "test_access": "mirrored",
            }
        },
        "canonical_exports": {"app.shared.errors.Base": "app.shared.Base"},
    },
    "style": {
        "imports": {"style": "absolute", "max_relative_level": 2},
        "reexports": {"value": "alias", "reason": "legacy packages"},
        "module_import_exemptions": ["facades"],
        "module_docstrings": "allow",
        "future_annotations": "forbid",
        "private_modules": "allow",
        "public_attribute_boundary": "module",
        "private_attribute_boundary": "package",
        "ordering": {"constants_exempt": True, "fix": "off"},
        "type_dispatch": {"style": "match", "min_branches": 4},
        "navigation": {"namespace_types": ["a.b.C"], "instance_factories": ["a.b.f"]},
        "call_argument_kinds": ["await"],
        "str_enum_values": "auto",
        "dataclass_options": ["frozen"],
        "bases": {"pydantic.BaseModel": "app.shared.Model"},
        "third_party_reexports": ["fastapi"],
        "errors": {"base": "app.shared.Error", "allow": ["ValueError"]},
        "forbidden_names": ["utils"],
        "file_naming": {"yaml_extension": "yml", "compose_basename": "compose"},
        "overrides": {
            "api": {
                "match": "app.features.*",
                "reexports": "all",
                "rules": {"CPX003": {"max": 3, "reason": "small interface"}},
            }
        },
    },
    "tests": {
        "layout": "mirror",
        "mirror": "{path}/test_{module}.py",
        "mirror_exclude": ["app/main.py"],
        "mirror_suffixes": True,
        "quality_exclude": ["tests/legacy/**"],
        "forbidden_mock_paths": ["tests/domain/**"],
        "assertion_helpers": ["tests.helpers.check"],
        "timeout_helpers": ["tests.helpers.wait"],
    },
    "rules": {
        "complexity/excess-positional-parameters": {"severity": "warning", "max": 4},
        "IMP002": {"max": 10, "reason": "large facade"},
        "architecture/uncovered-source": "error",
        "modeling": "off",
    },
    "ignore": [
        {
            "rule": "CPX003",
            "modules": ["app.legacy"],
            "paths": ["app/old.py"],
            "reason": "scheduled for removal",
        }
    ],
    "suppressions": {"require_reason": False},
    "debt": ".smelt/debt.json",
}
_WORKSPACE_ROOT: dict[str, Any] = {
    "version": 2,
    "project": {"workspace": "uv"},
    "architecture": {
        "members": {"packages/a": {"may_depend_on": ["packages/b"]}},
    },
    "style": {"reexports": "all", "module_docstrings": "allow"},
    "rules": {"CPX003": "warning"},
}
_WORKSPACE_MEMBER: dict[str, Any] = {
    "version": 2,
    "project": {"exclude": ["generated", {"path": "vendor", "reason": "vendored"}]},
    "style": {
        "module_docstrings": {"value": "require", "reason": "public library"},
        "overrides": {
            "inner": {
                "match": "a.core",
                "reexports": {"value": "alias", "reason": "legacy"},
                "rules": {"CPX003": {"max": 2}},
            }
        },
    },
    "rules": {"CPX005": "error", "CPX003": {"max": 3, "reason": "small api"}},
    "ignore": [{"rule": "CPX005", "paths": ["src/a/old.py"], "reason": "legacy"}],
    "architecture": {
        "layers": {
            "domain": {"modules": ["a.domain"]},
            "app": {"modules": ["a.app"], "may_depend_on": ["domain"]},
        },
        "packages": {"inner": {"match": "a.core", "public": ["."]}},
    },
    "debt": "debt.json",
}
_WORKSPACE_PYPROJECT = """\
[project]
name = "root"
version = "0"

[tool.uv.workspace]
members = ["packages/*"]
exclude = ["packages/skipped"]
"""
_MEMBER_PACKAGES = (
    "app/__init__.py",
    "packages/a/src/a/__init__.py",
    "packages/a/src/a/core/__init__.py",
    "packages/b/b/__init__.py",
    "packages/skipped/s/__init__.py",
    "tests/test_root.py",
    "packages/a/tests/test_a.py",
)
_PYTHON_LABELS = re.compile(
    r"\.(?:literal\[[^\]]*\]|function-after\[[^\]]*\]|RuleOverride|SourceExclusion"
    r"|CrossFeatureAllowance|str(?=:))"
)


@dataclass(frozen=True, slots=True)
class Case:
    label: str
    file: str
    text: str


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="*", type=Path)
    parser.add_argument("--cases", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=20261123)
    parser.add_argument("--show", type=int, default=12)
    parser.add_argument(
        "--native",
        type=Path,
        default=Path(sys.executable).with_name(
            "smelt.exe" if os.name == "nt" else "smelt"
        ),
    )
    args = parser.parse_args()
    native = args.native.resolve()
    tally = _Tally(limit=args.show)
    with tempfile.TemporaryDirectory(prefix="smelt-config-parity-") as temporary:
        root = Path(temporary).resolve()
        single = _project(root / "single", {"app/__init__.py": ""})
        for case in _single_cases(random.Random(args.seed), args.cases):  # noqa: S311
            tally.compare(single, case, native)
        workspace = _project(
            root / "workspace",
            {
                **dict.fromkeys(_MEMBER_PACKAGES, ""),
                "pyproject.toml": _WORKSPACE_PYPROJECT,
                "smelt.yml": _dump(_WORKSPACE_ROOT),
                "packages/a/smelt.yml": _dump(_WORKSPACE_MEMBER),
            },
        )
        for case in _workspace_cases(
            random.Random(args.seed + 1),  # noqa: S311
            args.cases,
        ):
            tally.compare(workspace, case, native)
        composed = _project(
            root / "composed",
            {"app/__init__.py": "", "base.yml": _dump(_SINGLE)},
        )
        for case in _composed_cases(
            random.Random(args.seed + 2),  # noqa: S311
            args.cases // 3,
        ):
            tally.compare(composed, case, native)
    for project in args.projects:
        path = project.resolve() / "smelt.yml"
        tally.compare(
            project.resolve(),
            Case(f"project {project.name}", "smelt.yml", path.read_text("utf-8")),
            native,
            restore=False,
        )
    _emit(tally.summary())
    return 1 if tally.different else 0


def _emit(line: str) -> None:
    sys.stdout.write(f"{line}\n")


def _dump(document: Any) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _project(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _paths(value: Any, prefix: tuple[Any, ...] = ()) -> Iterator[tuple[Any, ...]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield (*prefix, key)
            yield from _paths(child, (*prefix, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield (*prefix, index)
            yield from _paths(child, (*prefix, index))


def _parent(document: Any, path: tuple[Any, ...]) -> Any:
    for key in path[:-1]:
        document = document[key]
    return document


def _mutations(document: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    paths = list(_paths(document))
    for path in paths:
        shown = ".".join(str(part) for part in path)
        for value in _WRONG_VALUES:
            changed = copy.deepcopy(document)
            _parent(changed, path)[path[-1]] = copy.deepcopy(value)
            yield f"{shown} = {value!r}", changed
        changed = copy.deepcopy(document)
        del _parent(changed, path)[path[-1]]
        yield f"without {shown}", changed
    for path in [(), *paths]:
        target: Any = document
        for key in path:
            target = target[key]
        if not isinstance(target, dict):
            continue
        shown = ".".join(str(part) for part in path) or "<root>"
        for key in _UNKNOWN_KEYS:
            changed = copy.deepcopy(document)
            parent = changed
            for step in path:
                parent = parent[step]
            parent[key] = 1
            yield f"{shown} + {key}", changed


def _sampled(
    cases: Iterator[Case], generator: random.Random, limit: int
) -> Iterator[Case]:
    all_cases = list(cases)
    if len(all_cases) > limit:
        all_cases = generator.sample(all_cases, limit)
    yield from all_cases


def _single_cases(generator: random.Random, limit: int) -> Iterator[Case]:
    yield Case("valid single project", "smelt.yml", _dump(_SINGLE))
    minimal = {"version": 2, "project": {"root_packages": ["app"]}}
    yield Case("minimal project", "smelt.yml", _dump(minimal))
    yield Case("discovered packages", "smelt.yml", _dump({"version": 2, "project": {}}))
    for text in _STRUCTURES:
        yield Case(f"structure {text!r}", "smelt.yml", text)
    for scalar in _SCALARS:
        for key in ("reexports", "ordering", "call_argument_kinds"):
            text = (
                "version: 2\nproject: {root_packages: [app]}\n"
                f"style:\n  {key}: {scalar}\n"
            )
            yield Case(f"scalar {key}={scalar!r}", "smelt.yml", text)
        limit_rule = (
            "version: 2\nproject: {root_packages: [app]}\n"
            f"tests:\n  mirror_suffixes: {scalar}\n"
            f"rules:\n  CPX003:\n    max: {scalar}\n    severity: warning\n"
        )
        yield Case(f"scalar limit={scalar!r}", "smelt.yml", limit_rule)
    yield from _sampled(
        (
            Case(label, "smelt.yml", _dump(document))
            for label, document in _mutations(_SINGLE)
        ),
        generator,
        limit,
    )


def _workspace_cases(generator: random.Random, limit: int) -> Iterator[Case]:
    yield Case("valid workspace", "smelt.yml", _dump(_WORKSPACE_ROOT))
    yield Case("selected member", "packages/a/smelt.yml", _dump(_WORKSPACE_MEMBER))
    root_cases = (
        Case(f"root {label}", "smelt.yml", _dump(document))
        for label, document in _mutations(_WORKSPACE_ROOT)
    )
    member_cases = (
        Case(f"member {label}", "packages/a/smelt.yml", _dump(document))
        for label, document in _mutations(_WORKSPACE_MEMBER)
    )
    yield from _sampled(root_cases, generator, limit // 3)
    yield from _sampled(member_cases, generator, limit)


def _composed_cases(generator: random.Random, limit: int) -> Iterator[Case]:
    child: dict[str, Any] = {
        "extends": "base.yml",
        "style": {"reexports": "all", "overrides": {"api": {"reexports": "alias"}}},
        "rules": {"modeling": "warning", "CPX003": "off"},
        "ignore": [{"rule": "CPX005", "paths": ["app/x.py"], "reason": "new"}],
    }
    yield Case("valid extends", "smelt.yml", _dump(child))
    for parent in ("missing.yml", "smelt:strict", "/abs.yml", "a://b", "", "smelt.yml"):
        yield Case(
            f"extends {parent!r}",
            "smelt.yml",
            _dump({**child, "extends": parent}),
        )
    yield from _sampled(
        (
            Case(f"child {label}", "smelt.yml", _dump(document))
            for label, document in _mutations(child)
        ),
        generator,
        limit,
    )


class _Tally:
    def __init__(self, *, limit: int) -> None:
        self.limit = limit
        self.total = 0
        self.same = 0
        self.valid = 0
        self.labeled = 0
        self.crashed = 0
        self.different = 0

    def compare(
        self, root: Path, case: Case, native: Path, *, restore: bool = True
    ) -> None:
        path = root / case.file
        original = path.read_bytes() if restore and path.exists() else None
        try:
            if restore:
                path.write_bytes(case.text.encode("utf-8"))
            expected = _python_result(path)
            actual = _native_result(path, native)
        finally:
            if restore:
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
        self.total += 1
        if expected.get("crash"):
            self.crashed += 1
            if actual.get("ok"):
                self._report(case, "python crashed, rust accepted the file")
            return
        if not expected["ok"] and not actual["ok"]:
            expected["error"] = _normalized(expected["error"])
            actual["error"] = _normalized(actual["error"])
            if _PYTHON_LABELS.search(expected["error"]):
                difference = _labeled_difference(expected["error"], actual["error"])
                if difference is None:
                    self.labeled += 1
                else:
                    self._report(case, difference)
                return
        difference = _first_difference(expected, actual)
        if difference is None:
            self.same += 1
            self.valid += bool(expected["ok"])
            return
        self._report(case, difference)

    def _report(self, case: Case, difference: str) -> None:
        self.different += 1
        if self.different <= self.limit:
            _emit(f"DIFFERENT {case.label}\n  {difference}")

    def summary(self) -> str:
        return (
            f"{self.total} cases: {self.same} identical ({self.valid} valid), "
            f"{self.labeled} with Python-only union labels, "
            f"{self.crashed} where Python crashed, {self.different} different"
        )


def _normalized(message: str) -> str:
    message = re.sub(r"/blob/[^/]+/docs/upgrading", "/blob/REF/docs/upgrading", message)
    message = re.sub(r"invalid YAML:.*", "invalid YAML", message, flags=re.DOTALL)
    message = re.sub(r"(cannot read [^\n]*?): [^\n]*", r"\1", message)
    return re.sub(
        r"cannot discover workspace from pyproject\.toml: .*",
        "cannot discover workspace",
        message,
    )


def _labeled_difference(python: str, rust: str) -> str | None:
    """Rust omits Python's internal union branches; everything else must agree."""
    expected = {_PYTHON_LABELS.sub("", line) for line in python.splitlines()}
    actual = set(rust.splitlines())
    if extra := actual - expected:
        return f"only rust: {sorted(extra)}"
    # The string branch of a mapping entry carries no information about the entry.
    locations = {
        _PYTHON_LABELS.sub("", line).split(": ", 1)[0]
        for line in python.splitlines()
        if ".str:" not in line
    }
    if missing := locations - {line.split(": ", 1)[0] for line in actual}:
        return f"only python: {sorted(missing)}"
    return None


def _first_difference(left: Any, right: Any, path: str = "") -> str | None:
    if isinstance(left, dict) and isinstance(right, dict):
        return _mapping_difference(left, right, path)
    if isinstance(left, list) and isinstance(right, list):
        return _list_difference(left, right, path)
    return None if left == right else f"{path}: python {left!r} / rust {right!r}"


def _mapping_difference(
    left: dict[str, Any], right: dict[str, Any], path: str
) -> str | None:
    for key in left.keys() | right.keys():
        if key not in left or key not in right:
            return f"{path}.{key}: only in {'rust' if key in right else 'python'}"
        found = _first_difference(left[key], right[key], f"{path}.{key}")
        if found:
            return found
    return None


def _list_difference(left: list[Any], right: list[Any], path: str) -> str | None:
    if len(left) != len(right):
        return f"{path}: {left!r} != {right!r}"
    for index, (first, second) in enumerate(zip(left, right, strict=True)):
        found = _first_difference(first, second, f"{path}[{index}]")
        if found:
            return found
    return None


def _native_result(path: Path, native: Path) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603 - the locally built client
        [str(native), "--internal-config", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        return {"ok": False, "error": f"rust failed: {result.stderr}"}
    described: dict[str, Any] = json.loads(result.stdout)
    return described


def _python_result(path: Path) -> dict[str, Any]:
    try:
        loaded = load_config(path)
    except ConfigError as error:
        return {"ok": False, "error": str(error)}
    except Exception as error:  # Malformed input may crash the Python loader.
        return {"ok": False, "crash": repr(error), "error": repr(error)}
    described: dict[str, Any] = json.loads(json.dumps(_describe(loaded)))
    return described


def _dump_model(model: Any) -> Any:
    return model.model_dump(mode="json", by_alias=True)


def _contracts(structure: Any) -> dict[str, Any]:
    return {
        name: {
            "style_location": contract.style_location,
            "style_override": contract.style_override,
        }
        for name, contract in structure.contracts.items()
    }


def _rules(settings: Any) -> dict[str, Any]:
    return {
        key: _dump_model(value) if isinstance(value, RuleOverride) else value
        for key, value in settings.items()
    }


def _describe(loaded: Any) -> dict[str, Any]:
    config = loaded.config
    base = config.workspace_base
    return {
        "ok": True,
        "config": _dump_model(config),
        "structure": _dump_model(config.structure),
        "contracts": _contracts(config.structure),
        "members": {
            path: {
                "structure": _dump_model(member.structure),
                "contracts": _contracts(member.structure),
                "packages": list(member.packages),
            }
            for path, member in config.members.items()
        },
        "workspace_base": None
        if base is None
        else {
            "config": _dump_model(base),
            "structure": _dump_model(base.structure),
        },
        "workspace_packages": {
            path: list(names) for path, names in config.workspace_packages.items()
        },
        "rule_layers": [_rules(layer) for layer in config.rule_layers],
        "strict_coverage": config.strict_coverage,
        "warnings": [
            {"path": issue.path, "message": issue.message, "source": issue.source}
            for issue in loaded.warnings
        ],
        "path": loaded.path.as_posix(),
        "inputs": [path.as_posix() for path in loaded.inputs],
        "origins": dict(loaded.origins),
        "member": loaded.member,
        "rule_sources": list(loaded.rule_sources),
    }


if __name__ == "__main__":
    raise SystemExit(main())

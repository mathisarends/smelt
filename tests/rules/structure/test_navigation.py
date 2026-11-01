from pathlib import Path

import pytest

from smelt.diagnostics.debt import Debt
from smelt.engine.check import CheckOptions, snippet_reader
from tests.helpers import check, violations, write_project

OPTIONS = CheckOptions(select=("SMT322",))
BASE_CONFIG = "version: 1\nproject: {root_packages: [app]}\n"
CONFIG = BASE_CONFIG + "rules: {deep-object-navigation: hint}\n"


def _project(tmp_path: Path, source: str, config: str = CONFIG) -> Path:
    return write_project(
        tmp_path,
        {"smelt.yml": config, "app/__init__.py": "", "app/control.py": source},
    )


def test_repeated_navigation_reports_one_hint_per_whole_call(tmp_path: Path) -> None:
    source = "class Controller:\n    def run(self, width, height):\n        aim = self.browser.input.motor().aim(width, height)\n        motor = self.browser.input.motor()\n"
    root = _project(tmp_path, source)
    before = (root / "app/control.py").read_bytes()

    found = violations(root)

    assert [(v.code, v.line, v.column) for v in found] == [
        ("SMT322", 3, 15),
        ("SMT322", 4, 17),
    ]
    assert all(v.severity.value == "hint" for v in found)
    assert found[0].expected == {
        "class": "Controller",
        "method": "run",
        "callable": "self.browser.input.motor().aim",
        "stored_path": "self.browser.input",
        "stored_depth": 2,
        "automatic_rewrite": False,
    }
    assert (root / "app/control.py").read_bytes() == before


@pytest.mark.parametrize(
    "source",
    [
        "import sdk as api\nclass Controller:\n    def __init__(self): self.api = api\n    def run(self): self.api.chat.completions.create()",
        "from types import ModuleType as Module\nclass Controller:\n    def __init__(self, api: Module): self.api = api\n    def run(self): self.api.chat.completions.create()",
        "from types import SimpleNamespace as NS\nclass Controller:\n    def __init__(self): self.api = NS(chat=NS(completions=NS()))\n    def run(self): self.api.chat.completions.create()",
        "from dataclasses import dataclass as record\n@record\nclass State:\n    calls: list[str]\nclass Controller:\n    def __init__(self, state: State): self.state = state\n    def run(self): self.state.calls.append('run')",
        "from dataclasses import dataclass\n@dataclass\nclass State:\n    path: str\nclass Controller:\n    state: 'State | None'\n    def run(self): self.state.path.removesuffix('.py').replace('/', '.')",
        "from pathlib import Path\nclass Controller:\n    path: Path\n    def run(self): self.path.parent.mkdir(parents=True)",
        "import subprocess as processes\nclass Controller:\n    def __init__(self): self.process = processes.Popen([])\n    def run(self): self.process.stdin.write(b'run')",
        "from dataclasses import dataclass\nfrom functools import cached_property\n@dataclass\nclass State:\n    calls: list[str]\nclass Controller:\n    @cached_property\n    def state(self) -> State: ...\n    def run(self): self.state.calls.append('run')",
    ],
)
def test_statically_evidenced_namespaces_and_value_apis_are_allowed(
    tmp_path: Path, source: str
) -> None:
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "source",
    [
        "class Controller:\n    def run(self): self.api.chat.completions.create()",
        "class Service:\n    calls: list[str]\nclass Controller:\n    def __init__(self, service: Service): self.service = service\n    def run(self): self.service.calls.append('run')",
        "from dataclasses import dataclass\nclass Service: pass\n@dataclass\nclass State:\n    service: Service\nclass Controller:\n    state: State\n    def run(self): self.state.service.run()",
        "from types import SimpleNamespace as NS\nclass Service: pass\nclass Controller:\n    def __init__(self): self.api = NS(service=Service())\n    def run(self): self.api.service.run()",
        "import sdk as api\napi = object()\nclass Controller:\n    def __init__(self): self.api = api\n    def run(self): self.api.chat.run()",
        "import sdk as api\nclass Controller:\n    def __init__(self, api): self.api = api\n    def run(self): self.api.chat.run()",
        "import sdk as api\nclass Controller:\n    def __init__(self):\n        api = service\n        self.api = api\n    def run(self): self.api.chat.run()",
        "import sdk as api\nclass Controller:\n    def __init__(self): self.api = api\n    def change(self, service): self.api = service\n    def run(self): self.api.chat.run()",
        "from types import SimpleNamespace as NS\nclass Controller:\n    def __init__(self, NS): self.api = NS(chat=NS())\n    def run(self): self.api.chat.run()",
        "from types import ModuleType\nclass Controller:\n    @staticmethod\n    def prepare(other): other.api: ModuleType\n    def run(self): self.api.chat.run()",
        "from types import ModuleType\nclass Controller:\n    @classmethod\n    def prepare(cls): cls.api: ModuleType\n    def run(self): self.api.chat.run()",
        "import sdk as api\nclass Controller:\n    def __init__(self):\n        self = other\n        self.api = api\n    def run(self): self.api.chat.run()",
    ],
)
def test_namespace_spelling_services_shadowing_and_rebinding_do_not_exempt_calls(
    tmp_path: Path, source: str
) -> None:
    assert len(violations(_project(tmp_path, source), OPTIONS)) == 1


@pytest.mark.parametrize("kind", ["dataclass", "pydantic"])
def test_imported_record_changes_invalidate_namespace_evidence(
    tmp_path: Path, kind: str
) -> None:
    record = (
        "from dataclasses import dataclass\n@dataclass\nclass State:\n    calls: list[str]\n"
        if kind == "dataclass"
        else "from pydantic import BaseModel\nclass State(BaseModel):\n    calls: list[str]\n"
    )
    root = _project(
        tmp_path,
        "from .api import State\nclass Controller:\n    def __init__(self, state: State): self.state = state\n    def run(self): self.state.calls.append('run')",
    )
    write_project(
        root, {"app/api.py": "from .state import State\n", "app/state.py": record}
    )
    assert violations(root) == []

    # The unchanged caller must be reviewed when the dependency becomes a service.
    write_project(root, {"app/state.py": "class State:\n    calls: list[str]\n"})

    cached = violations(root)
    assert len(cached) == 1
    assert cached == violations(root, CheckOptions(use_cache=False))
    write_project(root, {"app/state.py": record})
    assert violations(root) == violations(root, CheckOptions(use_cache=False)) == []


@pytest.mark.parametrize(
    "expression",
    [
        "self.motor.aim(width, height)",
        "self.aim(width, height)",
        "self.browser.input.settings",
        "self.browser.input.settings = settings",
        "self.builder.with_width(width).with_height(height).build()",
        "self.make_builder().with_width(width).build()",
        "self.browser().input.motor().aim(width, height)",
        "browser.input.motor().aim(width, height)",
        "package.browser.input.motor()",
        "self.items[0].state.run()",
        "getattr(self.browser.input, 'motor')()",
        "lambda: self.browser.input.motor()",
        "[self.browser.input.motor() for item in items]",
        "def helper(self):\n            self.browser.input.motor()",
    ],
)
def test_direct_collaborators_data_and_uncertain_navigation_are_allowed(
    tmp_path: Path, expression: str
) -> None:
    source = f"class Controller:\n    def run(self):\n        {expression}\n"
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "source",
    [
        "class Controller:\n    async def run(this, /):\n        await this.browser.input.motor().aim()",
        "class Controller:\n    def run(self):\n        if ready:\n            self.browser.input.motor()",
        "class Outer:\n    def run(self):\n        class Inner:\n            def run(this):\n                this.browser.input.motor()",
    ],
)
def test_async_positional_receivers_control_flow_and_nested_classes(
    tmp_path: Path, source: str
) -> None:
    [finding] = violations(_project(tmp_path, source), OPTIONS)
    assert finding.code == "SMT322"


@pytest.mark.parametrize(
    "decorator", ["staticmethod", "classmethod", "property", "custom"]
)
def test_decorated_methods_are_excluded(tmp_path: Path, decorator: str) -> None:
    source = f"class Controller:\n    @{decorator}\n    def run(self):\n        self.browser.input.motor()"
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize("name", ["__new__", "__init_subclass__", "__class_getitem__"])
def test_implicit_class_and_static_methods_are_excluded(
    tmp_path: Path, name: str
) -> None:
    source = (
        f"class Controller:\n    def {name}(cls):\n        cls.browser.input.motor()"
    )
    assert violations(_project(tmp_path, source), OPTIONS) == []


@pytest.mark.parametrize(
    "binding",
    [
        "self = other",
        "del self",
        "for self in items: pass",
        "import other as self",
        "def self(): pass",
        "class self: pass",
        "try:\n            run()\n        except Error as self: pass",
        "match item:\n            case {'receiver': self}: pass",
    ],
)
def test_rebound_receiver_is_not_assumed_to_be_the_instance(
    tmp_path: Path, binding: str
) -> None:
    source = f"class Controller:\n    def run(self):\n        {binding}\n        self.browser.input.motor()"
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_module_functions_are_not_assumed_to_be_instance_methods(
    tmp_path: Path,
) -> None:
    source = "def run(self):\n    self.browser.input.motor()"
    assert violations(_project(tmp_path, source), OPTIONS) == []


def test_nested_arguments_are_checked_without_duplicate_receiver_findings(
    tmp_path: Path,
) -> None:
    source = "class Controller:\n    def run(self):\n        self.browser.input.motor(self.other.state.read()).aim(self.metrics.clock.tick())"
    found = violations(_project(tmp_path, source), OPTIONS)
    assert len(found) == 3
    assert {v.expected["stored_path"] for v in found if v.expected} == {
        "self.browser.input",
        "self.other.state",
        "self.metrics.clock",
    }


@pytest.mark.parametrize(
    ("expression", "depth"),
    [
        ("self.platform.browser.input.motor()", 3),
        ("self.browser.input.motor().aim().finish()", 2),
        ("wrap(self.browser.input.motor())", 2),
        ("self.browser.input.motor(width=self.options.values.read())", 2),
    ],
)
def test_deeper_chains_wrappers_and_keyword_arguments(
    tmp_path: Path, expression: str, depth: int
) -> None:
    source = f"class Controller:\n    def run(self):\n        {expression}"
    found = violations(_project(tmp_path, source), OPTIONS)
    assert len(found) == (2 if "width=" in expression else 1)
    assert found[0].expected is not None
    assert found[0].expected["stored_depth"] == depth


@pytest.mark.parametrize("selection", ["SMT322", "deep-object-navigation"])
def test_explicit_selection_enables_the_opt_in_hint(
    tmp_path: Path, selection: str
) -> None:
    root = _project(
        tmp_path,
        "class Controller:\n    def run(self):\n        self.browser.input.motor()",
        BASE_CONFIG,
    )
    assert violations(root) == []
    [finding] = violations(root, CheckOptions(select=(selection,)))
    assert finding.severity.value == "hint"


def test_closest_package_override_and_severity_are_respected(tmp_path: Path) -> None:
    source = "class Controller:\n    def run(self):\n        self.browser.input.motor()"
    config = (
        BASE_CONFIG
        + """structure:
  contracts:
    navigation: {match: app, rules: {deep-object-navigation: warning}}
    external: {match: app.external, rules: {deep-object-navigation: 'off'}}
"""
    )
    root = _project(tmp_path, source, config)
    write_project(root, {"app/external/__init__.py": "", "app/external/api.py": source})
    [finding] = violations(root)
    assert finding.path == "app/control.py"
    assert finding.severity.value == "warning"


def test_inline_suppression_and_rule_off_are_respected(tmp_path: Path) -> None:
    source = "class Controller:\n    def run(self):\n        self.browser.input.motor()  # smelt: ignore[SMT322] -- public SDK navigation\n"
    root = _project(tmp_path, source)
    outcome = check(root)
    assert outcome.report.violations == []
    assert outcome.report.suppressed == 1
    write_project(root, {"smelt.yml": CONFIG.replace(": hint", ": 'off'")})
    assert violations(root, OPTIONS) == []


def test_new_occurrences_are_not_hidden_by_existing_debt(tmp_path: Path) -> None:
    source = (
        "class Controller:\n    def run(self):\n        self.browser.input.motor()\n"
    )
    root = _project(tmp_path, source)
    initial = check(root)
    debt = Debt.from_violations(
        initial.report.violations, snippet_reader(initial.context)
    )
    write_project(
        root, {"app/control.py": source + "        self.browser.input.motor()\n"}
    )
    after = check(root)
    new, known, _ = debt.match(after.report.violations, snippet_reader(after.context))
    assert len(new) == len(known) == 1


def test_cached_and_uncached_results_agree_after_source_and_policy_changes(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        "class Controller:\n    def run(self):\n        self.browser.input.motor()",
    )
    assert len(violations(root)) == 1
    write_project(
        root,
        {
            "app/control.py": "class Controller:\n    def run(self):\n        self.motor.aim()"
        },
    )
    assert violations(root) == violations(root, CheckOptions(use_cache=False)) == []
    write_project(
        root,
        {
            "app/control.py": "class Controller:\n    def run(self):\n        self.browser.input.motor()"
        },
    )
    cached = violations(root)
    assert len(cached) == 1
    assert cached == violations(root, CheckOptions(use_cache=False))
    write_project(root, {"smelt.yml": CONFIG.replace(": hint", ": 'off'")})
    assert violations(root) == violations(root, CheckOptions(use_cache=False)) == []

# smelt

Static guardrails for Python packages, from small libraries to layered workspaces,
built for reviewing what coding agents change. Adopt maintainability conventions
and describe your architecture in `smelt.yaml`; `smelt check` reports findings
with the exact location, what is allowed instead and how to fix it:

- **Boundaries:** an import that crosses a layer, a feature or the composition root.
- **Package contracts:** required modules and explicit public entry points.
- **Coverage:** production code without a declared architecture policy.
- **Test mirroring:** a test file that does not sit at the mirrored path of a source module.
- **Maintainability:** public definitions first, internal helpers exposed as public,
  and concrete test-quality review candidates.

## Installation

Requires Python 3.12 or newer.

```bash
uv tool install smelt-cli
# or install into your project's environment:
pip install smelt-cli
```

The PyPI package is named `smelt-cli`; the CLI command and Python package are both
named `smelt`. To run without installing, use `uvx --from smelt-cli smelt check`.
From a local checkout, use `uv tool install .`. The PyPI package named `smelt` belongs
to a different project; `pip install smelt` and bare `uvx smelt` install that project.

## Small packages and explicit checks

Small packages need no features or layers. Start from the project root:

```bash
smelt init --maintainability
smelt check --show-hints --fail-on warning
```

`init --maintainability` writes individually named checks into `smelt.yaml`.
There is no runtime preset. The file records exactly which conventions the repo
adopts, from public-first ordering and internal helpers to test outcome reviews.
A small `src/` package can use:

```yaml
# yaml-language-server: $schema=.smelt.schema.json
version: 1
project:
  root_packages: [harnessed]
  source_roots: [src]
  test_roots: [tests]

rules:
  public-definitions-first: error
  internal-public-member: warning
  internal-static-helper: hint
  test-without-outcome-check: warning
  unbounded-test-wait: warning
  async-mock-without-await-check: warning
```

For a flat package, use `source_roots: ["."]`. `init` also discovers namespace
packages and uv workspace members, and keeps inferred architecture when layers
exist. Named rules apply without additional boolean switches. Genuine options,
package overrides, architecture policies and reasoned exclusions remain explicit.

`init` also writes a local `.smelt.schema.json` for editor completion, validation
and hover examples. The checker reads only the YAML, not this editor artifact.
See [configuration, editor setup and breaking-change migration](docs/configuration.md).

## Usage

See the [changelog](CHANGELOG.md) for new capabilities, fixes and migration notes
by release, including changes to agent workflows and rule defaults.

Most rules are opt-in. Smelt enables declared architecture/package/test policies
without a second switch; only unknown-layer and suppression/debt diagnostics run
by default. Import cycles and unclassified-module hints require explicit selection.
See [rule activation and migration](docs/rule-selection.md) and the
[full catalog](docs/rules/README.md) for the decisions and precedence rules.

```bash
smelt init                           # draft smelt.yaml from the code (also uv workspaces)
smelt init --maintainability         # write individually named source/test review checks
smelt init --config other.yaml       # write a config and editor schema in that directory
smelt check                          # whole project
smelt check --changed                # only what the working tree introduced since HEAD
smelt check --changed --base origin/main --format json
smelt check --strict-coverage         # fail on omitted or unclassified production code
smelt check --show-coverage --format json  # include the per-file policy inventory
smelt context user                   # what applies to a feature, module or path
smelt explain SMT101                 # rationale, examples and config keys of a rule
smelt rules                          # full catalog with activation and severity
smelt debt                           # record today's violations as known debt
smelt debt --prune                   # drop debt entries that were fixed
smelt config show                    # the resolved config
smelt config schema                  # the JSON schema of smelt.yaml
```

Exit codes: `0` clean, `1` violations at or above `--fail-on`, `2` config or usage error.
A config entry that points at nothing (a mistyped package, a missing test root) is a
config error, not a silently disabled rule.
Explicit check paths must exist and contain analyzed source or test files; unknown
`--select`/`--ignore` selectors are usage errors. JSON includes the reporting scope, file
count and active rule codes. `--config PATH` works before or after a subcommand.
A stored JSON report explains itself: `scope.mode` is `full` or `changed`, and
`comparison` names the requested `base`, the resolved `merge_base`, the
`compared_revision` and `head`, so `passed` from `--changed` reads as "nothing new since
that commit", not as a clean inventory. `coverage` lists the source and test roots,
workspace members that are not analyzed, module counts per classification, the
composition-root and wiring exemptions, third-party policies per layer, the import
settings (`type_checking`, `transitive`, `cycles`), cross-feature allowances as fields,
and a `policy_hash` that is equal for reports checked under the same resolved config.
The report's `project` names the config, project root and repository root. JSON
finding paths are project-relative; SARIF and GitHub annotation paths are rebased
to the repository root, including when using a nested `--config`.
`smelt context --format json` carries the same import settings, allowances and hash.
With `--format json`, an exit `2` prints a JSON document as well, so an agent needs no
text parser: `{"schema_version": 1, "status": "error", "error": {...}}` with `kind`
(`usage`, `config` or `analysis`), `message`, the rejected `input` (`option`, `value`)
and, for config errors, the `config` file with each issue's YAML `location`. That covers
argument errors (`--fail-on bogus`, an unknown flag) whenever `--format json` is on the
command line; an invalid `--format` value itself is reported as argparse text.

Valid configurations can also produce warnings, such as cyclic `may_depend_on`
policies. Reports retain these separately from rule violations in JSON
`configuration_diagnostics` (config path, key, severity and message) and SARIF
`toolConfigurationNotifications`. Text and GitHub output include them too.
They count toward the warning total and cause exit `1` with `--fail-on warning`.
Configuration warnings describe the current policy even in scoped/changed checks;
rule selection, source suppressions and accepted debt do not remove them.

`--changed` checks the base commit (HEAD, or the merge-base with `--base`) with today's
config and reports only violations that are new, wherever they show up. Old violations in
an edited file stay quiet, a new import cycle does not.
Changing an import's comment, formatting or alias does not make its existing boundary
violation new; an additional identical violating import still counts as new.

## A DDD layout

```yaml
version: 1
project:
  root_packages: [backend, agent]
  source_roots: [backend/src, libs/agent/src]
  test_roots: [backend/tests, libs/agent/tests]

architecture:
  features: {root: backend.features}     # every child package is a feature
  shared: [backend.shared, backend.env]  # importable everywhere, imports no feature
  composition_root: [backend.main, backend.app]
  wiring: [backend.features.*.infrastructure.di]
  modules:                               # packages outside the features
    backend.platform: infrastructure
    agent: infrastructure
  layers:
    domain: {path: domain}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}
    presentation: {path: presentation, may_depend_on: [application, domain]}
  cross_feature:
    default: deny
    allow:
      - "application -> application"     # between all features
      - {from: session.presentation, to: auth.presentation}

tests:
  layout: mirror
  mirror: "{root}/{path}/test_{module}.py"
```

- **Layers** hold inside every feature: `domain` must not import `infrastructure`.
- **Features** may only import each other through `cross_feature.allow`. The string form
  allows a layer pair for all features, the object form one direction between two.
- **Central packages** such as a platform or a workspace library get a layer under
  `modules`. The layer rules then apply between them and the features (a feature's domain
  must not import `backend.platform`), and they must not import features.
- **Wiring** modules (whole-segment `*` allowed) keep their feature and layer and may cross
  layer boundaries. Feature wiring may also cross feature boundaries. Composition roots,
  other wiring modules and their package facades may import declared wiring. Central or
  shared wiring keeps the prohibition on importing features.
- Every module should belong somewhere: an unclassified module is exempt from all
  layer and feature boundary checks, so SMT305 warns about it. Composition-root/wiring
  import restrictions and configured cycle checks still apply.

`smelt init` infers most of this: features, layers, shared and settings modules, the
composition root including an app factory (imported or named as a `"backend.app:app"`
server target), wiring patterns, central packages by name and
the mirror pattern the existing tests follow. Review it before adopting its findings.
It follows the names a composition root imports through package facades into their
definitions (`FEATURES = (chat.feature, ...)`); a module whose definition is built from
providers, such as a `feature.py` assembling `TelegramProvider`, becomes wiring, and the
config comment shows the chain. Merely importing framework types changes nothing. An
unclassified package only the root uses is proposed as a commented-out candidate with its
chain and confidence, never applied silently.
In a uv workspace it lists every declared member with the packages it found or why it
skipped it (`tool.uv.workspace.exclude`, no Python package); namespace packages without
`__init__.py` count under `src/` and where `tool.uv.build-backend` declares them.
Its starter policy explicitly allows third-party packages and checks direct imports.
Review these decisions: to keep frameworks out of the core, set e.g.
`domain.third_party: {default: deny, allow: [pydantic]}` under `architecture.layers`.
Set `architecture.imports.transitive: true` to also detect layer dependencies through
re-exports, such as a domain importing an infrastructure provider from a feature's
`__init__.py`. Named re-exports also retain their feature, shared and composition-root
restrictions. The starter leaves `cross_feature.allow` empty; declare intended
feature relationships explicitly. A global `application -> application` pair permits
that dependency between **every** feature.

`smelt context` describes the policy that applies to its target, including wiring and
composition-root exceptions, third-party defaults and direct/transitive coverage. It
accepts feature names, dotted module names and source paths, including planned `.py`
files under existing source packages. If a syntax error prevents analysis, the briefing
is still available and its violation counts are marked unavailable (`violations: null`
and `analysis_error` in JSON). Central and wiring modules have explicit JSON flags.

## Strict policy coverage

Enable `project.coverage: strict`, or use `smelt check --strict-coverage`, to fail
when production source escapes package selection or has no architecture role.
Folder names, layer names and frameworks come from your policy. Flat libraries,
feature-only architectures and custom layers work without adopting a DDD layout.
Outside uv workspaces, declare the relevant import roots in `source_roots`.
Unreadable or invalid workspace metadata is an analysis error in strict mode;
configured mode records discovery uncertainty and marks coverage incomplete.
The inventory discovers Python files independently of `root_packages`, under the
configured source roots and declared uv workspace members. Packaging exclusions
in `tool.uv.workspace.exclude` do not hide source from this inventory.

```yaml
project:
  root_packages: [backend, client]
  source_roots: [backend/src, libs/client/src]
  coverage: strict
  exclude:
    - path: "libs/client/src/client/generated/**"
      reason: "Generated SDK; regeneration and drift are checked separately."
```

Each discovered source has one status:

- `governed`: a declared layer, feature-only policy, shared, central, wiring or
  composition-root role applies.
- `facade`: a package initializer containing imports, documentation and package
  metadata, including guarded imports and the standard package-version lookup.
  Implementation functions and classes in an unclassified initializer are gaps.
- `unclassified`: analyzed source has no architecture role.
- `uncovered`: source exists outside the selected packages or is not an importable
  module. The diagnostic names its package and import root where possible.
  Loose scripts need a governed package or a reasoned tooling exclusion;
  `root_packages` selects package directories rather than individual Python files.
- `excluded`: an explicit project exclusion applies. Strict mode requires a
  `{path, reason}` entry with a nonempty reason; string globs remain supported in
  the default `configured` mode.

`coverage.complete` means every discovered production source has a policy, is a
package facade, or has a reasoned exclusion. It describes policy assignment;
`scope.rules` and `coverage.imports` describe the checks actually run. Correct
behavior still needs type checking and tests. Configured test roots, workspace
`tests/` and `test/` directories, hidden directories and environment/cache
directories are outside production discovery. Independent runtimes outside the
declared roots/workspace need their own configuration and check.

The normal JSON report includes counts and scan roots. Add `--show-coverage` for
the per-file inventory, including feature, layer, source root, policy references
and exclusion reason. Text output groups related missing files into one decision
while JSON retains every path. The text summary also reports incomplete policy
assignment in `configured` mode, which preserves the existing exit behavior.

Strict coverage findings (`SMT306`) remain errors even in `--changed`, path-scoped
and `--select` runs. Inline/config ignores and debt cannot hide them; disabling or
demoting SMT306 in strict mode is rejected. Recording or pruning debt requires
complete coverage first. Resolve the placement or document a deliberate exclusion.

`smelt context <path> --format json` includes the file's coverage status and policy.
It also explains uncovered source discovered outside `root_packages`. A planned
path reports whether its placement has a role without claiming the file has been
analyzed. Use this before writing code, then run the strict check after editing.

## Package contracts

Declare a repeated structure or public interface explicitly. Contracts apply to
Python packages matched by a dotted pattern, independently of feature or layer
names. A whole-segment `*` in `match` selects one package segment.

```yaml
structure:
  contracts:
    feature-shape:
      match: app.features.*
      required: [application.api, domain]
      public: [application.api]
    library-api:
      match: toolkit
      required: [models]
      public: ['.']
```

This requires every matching feature to contain an `application.api` module or
package and a `domain` module or package. Callers outside that feature may import
only `application.api`. Internal code may use its own implementation modules.
`toolkit` exposes its package facade; `toolkit.models` remains private to outside
callers, while the facade can deliberately re-export symbols from it.

- `required` contains exact relative dotted module names. Namespace packages and
  split source roots work too. Excluded files do not fulfill a contract.
- `public` entries are relative module patterns. `'.'` means the matching package
  itself; `api` exposes exactly that module/package, and `api.**` deliberately
  exposes that subtree. Each pattern must match analyzed code. Omit `public` to
  check structure only; `public: []` closes the package to outside imports.
- Structure and interface contracts are additive: an import must satisfy every applicable contract.
  Wiring and composition roots follow the same interface rules. Existing layer
  and feature restrictions still apply, and public entries do not classify code
  into architecture roles or grant cross-feature access.
- SMT307 reports missing required modules or public entry points. A `match`
  pattern selecting no analyzed package is a configuration error. SMT107 reports
  private imports, including explicit module-valued aliases through facades;
  intentional symbol re-exports through a public entry point are permitted.
- These are static module contracts. They do not check `__all__`, arbitrary
  attribute access, dynamic loading, symbol-level visibility, or behavior. Module
  presence alone does not establish a useful implementation; choose contracts
  that reflect an intentional design, rather than creating empty template files.

`smelt context` includes applicable contracts, required/public modules, missing
entries and the visibility of an existing or planned module. Check JSON includes
resolved contracts under `coverage.contracts`. `smelt explain SMT107` and
`smelt explain SMT307` describe the rules. No contract is inferred automatically
or enabled by default for consumer projects.

### Module names without private prefixes

Require descriptive module/package names while keeping private helpers inside
them:

```yaml
structure:
  contracts:
    module-names:
      match: app
      private_modules: forbid
```

SMT312 (`no-private-modules`) rejects `_service.py`, `_internal/` and other
underscore-prefixed module/package names, including double-underscore names.
Python's special `__init__.py` and `__main__.py` files are allowed; arbitrary names
such as `__service__.py` are still checked. This exception applies to files, not
dunder-named package directories.

Use `service.py` with public entry functions and `_helper` functions instead.
The rule checks names independently of exports: a private-prefixed module is
still a finding when it declares `__all__` or its symbols are re-exported. Function,
method, class and attribute names are unaffected. This naming convention does
not change the public entry points declared by architecture API contracts.

Omitting `private_modules` adds no naming policy. A more specific package can set
`private_modules: allow`; contradictory policies on the same concrete package
are config errors. Existing reasoned `ignore` entries can cover individual legacy
modules. Excluded source and test directories follow the normal analysis scope.
Namespace packages and split import roots are supported, with one finding per
offending logical package rather than duplicate findings for its portions.

`context` exposes the effective convention as `naming_policy`, including planned
files. Check JSON includes it in `coverage.contracts`. Rename deliberately and
update imports and facade exports; Smelt never renames files or symbols itself.

### Separate instance attribute assignments

To keep field initialization readable, opt into `SMT314`:

```yaml
structure:
  contracts:
    instance-fields:
      match: app
      rules:
        bundled-instance-assignment: error
```

This reports `self.log, self.control = log, control` and chained assignments such
as `self.log = self.control = value`. Prefer:

```python
self.log = log
self.control = control
```

The rule checks assignments writing multiple direct attributes of an instance
method's first positional parameter, including differently named receivers and
async methods. Local unpacking stays valid. Mixed unpacking with only one direct
instance target is allowed; nested object attributes and assignments in nested
helper bodies are outside the check. Nested classes use their own receiver.
Recognized static/class methods are excluded; arbitrary decorator and assignment
aliases are not inferred. Only analyzed production modules are covered.

The closest package rule wins; `rules: {bundled-instance-assignment: off}` exempts
a package. Effective rule severities appear in `context`, including planned modules.
This is an optional readability convention. There is no autofix: parallel
assignment, properties, swaps and unpacking failures can change behavior when
split. When unpacking a computed pair, consider unpacking into locals first and
then assigning each field while preserving evaluation timing.

### Type dispatch with match/case

`SMT315 prefer-match-type-dispatch` suggests reviewing longer positive
`isinstance`/`elif` chains over the same simple name:

```yaml
structure:
  contracts:
    event-dispatch:
      match: app
      type_dispatch:
        style: match
        min_branches: 3
```

The default threshold is three type-tested branches; it counts branches rather
than members of a type tuple/union. The closest policy wins. Use `style: allow`
for an exception and `rules: {SMT315: error}` if this convention must fail CI.
The default severity is a hint, since class-pattern equivalence needs review.

```python
match event:
    case OutputValidated() | OutputValidationFailed():
        if event.call_id is not None:
            control_calls.add(event.call_id)
    case ToolCalled() if event.call.id not in control_calls:
        calls[event.call.id] = event.call
        yield ToolStarted(event.call)
```

An independent preceding `if isinstance(event, SessionEvent): events.append(event)`
must stay independent: an event may need both observation and dispatch. An inner
body condition must not become a case guard without checking fallthrough: when
an OutputValidated event has no call ID, its original branch still consumes it.
Existing and-guards are recorded separately from body conditions.

Supported heads use the builtin isinstance or an unambiguous top-level builtins
import alias, a simple subject name and type names/dotted names/tuples/unions.
Additional and-guards are allowed. Independent if statements, mixed subjects,
negative/or tests, type factories, parameterized types, computed subjects and
unsupported branches are not combined. One hint describes the whole supported
chain; elif suffixes are not reported independently.

Checker names bound anywhere in a file are conservatively excluded unless they
are a single recognized top-level builtin import. A parameter in another function
can therefore suppress the same checker spelling for the file. Star imports and
recognized builtin-module mutations also disable detection. Assignment aliases,
dynamic namespace changes and runtime type identities are not inferred; a type
name may refer to something unsuitable for a class pattern.

There is no autofix. Match evaluates its subject once, and subclasses, custom
instance checks, guard effects and runtime type values need manual review. Declared
Python minima before 3.10 are excluded; unknown minima retain an explicit version
review requirement. JSON exposes the subject, branch types, guards and Python
target; context exposes the policy for existing and planned modules. Only analyzed
production sources are covered. Changed/debt comparisons track branch identities
and multiplicity, so an additional branch cannot hide behind an accepted chain.
See the Python reference for
[match semantics](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement).

### Relative and absolute imports

Declare the style explicitly, globally for a root package or for selected
packages. This example uses absolute imports by default and relative imports
within infrastructure packages where at most one leading dot is needed:

```yaml
structure:
  contracts:
    default-style:
      match: app
      imports: {style: absolute}
    adapter-style:
      match: app.features.*.infrastructure
      imports: {style: relative, max_relative_level: 1}
```

In the relative scope, `from .models import Record` is preferred over
`from app.features.users.infrastructure.models import Record`. An import requiring
`from ..domain import User` instead uses the absolute path. The limit counts
leading dots; set `max_relative_level: 2` to permit and prefer parent imports up
to two dots. Imports between different root packages and third-party/standard
library imports stay absolute. `style: absolute` requires absolute imports even
for siblings. Omitting `imports` preserves either form.

The most specific enclosing package with an `imports` policy wins. Conflicting
styles for the same concrete package are config errors. Initializers and namespace
packages use their logical package, independently of filesystem source roots.
SMT308 reports the expected form without rewriting bindings or aliases. As with
other import rules, `architecture.imports.type_checking` controls guarded imports.
Ruff's [TID252](https://docs.astral.sh/ruff/rules/relative-imports/) already covers
forbidding parent-relative imports; Smelt additionally checks the preferred form
under these package contracts.

### Suggestions to bundle implementation imports

Several imports from the same package can suggest a missing public facade:

```python
from app.features.users.application.get_user import GetUser
from app.features.users.application.update_user import UpdateUser
from app.features.users.application.delete_user import DeleteUser
```

Enable an advisory threshold on the package that would own the common API:

```yaml
structure:
  contracts:
    application-api:
      match: app.features.*.application
      bundle_imports_at: 3
```

SMT108 suggests the matching `application` package facade once one outside file
imports at least three distinct implementation modules there. Repeated imports,
multiple symbols from one module, and imports in separate callers do not inflate
the count. Imports from inside the package and symbols already exported by its
facade do not count. The threshold is configurable and disabled when omitted.

After deliberately defining the exports, the caller can use a combined import
such as `from app.features.users.application import DeleteUser, GetUser, UpdateUser`.
Review dependency direction and cycles before adding exports. This is a `hint`
by default; it does not fail the usual error gate. Choose `public: ['.']` instead
to require facade-only imports regardless of their count. No facade or import is
rewritten automatically.

### Module headers and Python-version-aware annotations

Module docstrings and future imports can be governed independently:

```yaml
# Optional override of the minimum supported version, when metadata is absent:
project:
  python_version: "3.14"

structure:
  contracts:
    source-conventions:
      match: app
      module_docstrings: forbid
      future_annotations: when-needed
```

`module_docstrings: forbid` enables SMT309 for the initial module docstring,
including package initializers. Class/function documentation and ordinary string
values are unaffected. Use `allow` for an exception in a more specific package;
omitting the field adds no policy.

`future_annotations` supports three modes:

- `allow`: preserve the import, including a deliberate runtime compatibility use.
- `forbid`: SMT310 rejects `from __future__ import annotations` at any version.
- `when-needed`: SMT310 rejects it for a declared minimum of Python 3.14 or newer.
  With an earlier or unknown minimum, SMT311 gives a conservative review hint for
  modules with no detected postponement need. It retains imports for unresolved
  or quoted forward references, `TYPE_CHECKING`, runtime annotation inspection,
  decorators, inherited models and unknown annotation expressions. Builtin
  generics and unions are considered with their supported Python version.

Version detection uses the nearest project's `requires-python` in
`pyproject.toml`, with an explicit `project.python_version` taking precedence.
The configured version means the **minimum supported version**. The interpreter
running Smelt and `.python-version` are not evidence that older Python releases
are unsupported. Monorepo members keep their own declared requirements; an
independent member with missing/invalid metadata stays unknown. Lower bounds are
validated with Python packaging specifiers; exclusions alone never raise the
inferred minimum. For external source roots, discovery also checks their import
root's parent; declare an override when the project boundary is otherwise unknown.

The report exposes versions, metadata provenance and uncertainty under
`coverage.python_targets`; context JSON includes `python_target` and the effective
`header_policy`, and findings explain which version was used.

Python 3.14 [defers annotations by default](https://docs.python.org/3.14/whatsnew/3.14.html#deferred-evaluation-of-annotations),
but the future import still changes their runtime representation. These are
source conventions and advisory evidence, not proof of equivalent runtime
behavior. Smelt never removes imports or documentation automatically. Verify
annotation consumers and tests before removing an import, and use an explicit
package exception where its string-based behavior is intentional.

### Definition order with execution dependencies

Enable `SMT313 public-definitions-first` to put the public interface first while keeping
prerequisites, binding versions and runtime boundaries intact:

```yaml
structure:
  contracts:
    definition-order:
      match: app
      ordering:
        module:
        - docstring
        - future-imports
        - imports
        - dunder-assignments
        - type-vars
        - public
        - private
        - main-guard
        class:
        - class-attributes
        - init
        - dunders
        - public
        - private
        constants_exempt: false
        fix: safe-only
      rules:
        public-definitions-first: error
```

The defaults are shown above. Each list must contain its categories exactly once;
module docstrings, future imports and imports form the fixed prefix, and the
main guard belongs last. Use package `rules: {public-definitions-first: off}` to exempt a package.
The closest explicit rule setting wins; ordering options alone do not activate the check. This extends `smelt.yaml` package contracts rather than adding
a second `[tool.smelt.ordering]` configuration source.

The analyzer partitions module and directly nested class bodies into sections.
Within each section it computes a stable topological order: configured priority,
with prerequisites inheriting the priority of their dependents. Relative order
inside a category stays unchanged. Functions may refer to helpers in their bodies
before those helpers are defined. Decorators, defaults, class creation,
assignments and eagerly evaluated annotations have different timing; existing
reads and later rebindings are preserved. Unknown Python minima are treated
conservatively as eager; the future import or a declared Python 3.14+ minimum
defers annotations, while defaults remain eager.

Calls, unknown decorators, imports, control flow, `TYPE_CHECKING`, mutations,
operator/protocol evaluation and unsupported generic constructs are execution
barriers. Smelt keeps them in place. Literal construction through recognized
typing TypeVar/ParamSpec/TypeVarTuple factories has a dedicated category.
Consecutive overload/implementation and property getter/setter/deleter families
remain atomic. Noncontiguous property families are pinned. Function bodies,
including classes defined inside them, are outside this ordering analysis.

`constants_exempt: true` treats private uppercase module constants like public
definitions, allowing them to remain above the interface. Class attributes have
their own category. `__init__`, `__new__` and `__post_init__` share the initializer
category; other dunders precede ordinary public/private methods by default.
Names use the underscore convention; ordering does not infer semantic visibility
or rename symbols. If another tool changes visibility, run it before ordering.

Plain `smelt check` is always read-only. Request fixes explicitly:

```bash
smelt check --select SMT313 --format json
smelt check --select SMT313 --fix --format json
```

The fixer applies only visible, non-suppressed, non-debt sections within the
requested scope, then checks again. `fix: off` disables writes for a package.
Changed checks operate on affected reported sections, including any existing
ordering debt in a section that also contains a new inversion. Debt pruning and
changed comparisons track inversion identities, so adding a new inversion cannot
hide behind an existing aggregate finding.

Bounded fixes move source blocks, comments and their leading whitespace together;
they preserve UTF-8/BOM, newline convention and file permissions. Nested edits
are rendered together and the resulting AST must equal the approved permutations.
Use the project's formatter afterwards to normalize spacing. Symlinks, external
source roots, concurrent edits, formatter/suppression regions, tab indentation,
runtime introspection and uncertain class/decorator behavior require manual
review. A misplaced main guard or import is diagnosed but never moved across an
execution barrier automatically. These bounds do not prove arbitrary Python
reflection or hidden runtime effects equivalent; verify types and behavior.

JSON includes expected/original order, semantic inversions, fixability and reasons.
`coverage.ordering` identifies the analyzed files and barriers; `context` exposes
the effective `ordering_policy`, including planned placement. With `--fix`, JSON
also includes `fixes.ordering` with modified files, section counts and skipped
reasons. Every other rule keeps its existing behavior; no visibility or module
rename fixer is implied by this option.

### Internal public functions, methods and static helpers

Adopt visibility and helper-placement conventions per package:

```yaml
structure:
  contracts:
    internal-helpers:
      match: app
      rules:
        internal-public-member: warning
        internal-static-helper: hint
    deliberate-api:
      match: app.integrations
      rules:
        internal-public-member: 'off'
        internal-static-helper: 'off'
```

[SMT316](docs/rules/SMT316.md) reviews public module functions and ordinary instance
methods with no observed use outside their defining module or class, including
unused definitions. For example, `emit` called or passed as `self.emit` only
inside `Browser` gets a private-name suggestion such as `_emit`. The JSON lists
internal reference lines and distinguishes unused members from internal helpers.
Existing private-name collisions get a distinct suggested name. Explicit module
exports, decorated members and inherited/subclassed classes are preserved.
Discovered test accesses count as external uses; SMT429 separately reviews
test-only public module functions when that named rule is enabled.

[SMT317](docs/rules/SMT317.md) suggests moving an internal `@staticmethod` helper
to a private module function, such as `Browser._normalize(value)` becoming
`_normalize(value)`. It supports sync/async helpers and builtin decorator aliases;
class-namespace dependencies, inheritance, extra decorators and possible external
method accesses are excluded. Neither rule performs automatic changes.

Both named rules use the closest explicit package rule setting. They are advisory:
unknown same-named attribute access outside a class conservatively preserves its
methods, and external users or dynamic reflection cannot be ruled out. Review
the intended API before renaming or extracting a helper. `smelt context` includes
the effective policies even for planned files.

## Test policies

### Optional test quality policies

Test mirroring and test quality are independent. Enable the policies that match
your unit-test conventions in the existing YAML configuration:

```yaml
tests:
  layout: mirror
  forbid_mocks_in:
  - '**/tests/**/domain/**'
  max_patches_per_test: 2
  assertion_helpers:
  - tests.assertions.assert_user
  timeout_helpers:
  - tests.timing.deadline
  quality_exclude: []
rules:
  private-test-access: error
  string-patch-target: error
  test-without-outcome-check: warning
  test-only-checks-possibly-empty-data: warning
  unbounded-test-wait: warning
  independent-test-case-loop: hint
  async-mock-without-await-check: warning
  test-only-public-function: warning
```

| Code | Checks |
| --- | --- |
| SMT420 | Private production imports, module access and direct known/typed instance reads |
| SMT421 | Recognized mocking operations in the configured test paths, including conftest fixtures |
| SMT422 | Literal string targets of patch, patch.dict, patch.multiple and MonkeyPatch setattr/delattr |
| SMT423 | Recognized patch calls exceeding the per-test limit, including decorators, patch.object and MonkeyPatch attribute replacement |
| SMT424 | No recognized explicit outcome assertion (warning by default) |
| SMT425 | Every recognized outcome can disappear or pass trivially on empty input (warning) |
| SMT426 | Known waits without local deadlines or replacement of the asyncio scheduler clock (warning) |
| SMT427 | Small literal loops containing candidate independent cases (hint) |
| SMT428 | Call-only assertions on known async mocks without corresponding await evidence (warning) |
| SMT429 | Public module functions accessed from tests but not from other analyzed production modules (warning) |
| SMT430 | Public instance/class data attributes read from tests but not accessed by production code outside their class (warning) |

Every policy is opt-in; `0` is a valid patch limit. Globs are relative to the
configuration root, not each test root. `quality_exclude` does not disable mirror
checks or exclude production code. Rule selection, severity overrides, inline
suppressions and debt work normally. Plain checks remain read-only.

The shared analysis recognizes unittest.mock import aliases and the standard
pytest-mock fixtures (`mocker`, `class_mocker`, `module_mocker`, `package_mocker`,
`session_mocker`) on test/fixture functions. Explicit MockerFixture annotations
are also recognized. MonkeyPatch receivers include the standard `monkeypatch`
fixture, explicit annotations, constructors, aliases and `.context()` scopes.
Only its `setattr`/`delattr` attribute replacements count as patches; environment,
mapping and directory setup remain separate. Local shadowing and common
control-flow bindings are respected. Locally declared fixture overrides take
precedence over standard fixture names; external plugin overrides remain unknown.

SMT424 accepts assert other than literal `assert True`, recognized pytest
exception/warning checks, unittest.TestCase assertions, known mock interaction
assertions and imported qualified assertion helpers. A mere unused
`pytest.raises(...)` context object supplies no check. Known Mock/AsyncMock
constructors, simple aliases, default patch context results and explicitly typed
fixtures establish mock receivers; arbitrary methods named `assert_*` do not.
An awaited `asyncio.wait_for(known_event.wait(), timeout=...)` or an equivalent
Event wait inside a timeout context that raises on expiry also supplies
observable lifecycle evidence. `move_on_after` and configured timeout helpers
alone do not. A frozen scheduler clock can still defeat that
deadline. Assertions inside a nested helper or fixture are not attributed to a
caller. Positive validation/smoke tests can justify an exception. `assert result`
still counts: this rule detects missing explicit checks, not assertion quality,
reachability or correct behavior. Neither a literal threshold nor syntax proves
that a test protects a requirement.

SMT425 complements outcome recognition: if every recognized check is inside a
possibly empty result loop, or is a positive `assert all(...)` over possibly empty
input, it asks for review. Nonempty literal collections, immutable tuple/string
aliases and literal builtin `range` calls are recognized. Mutable collection
aliases remain unknown because other calls can empty them. An additional independent outcome,
explicit empty-result comparison, or alternative empty case is retained. Prefer
complete generated/expected mappings over loops that check only returned files.
This check does not prove collection cardinality or assertion reachability.

SMT426 checks known subprocess calls for their own timeout, and known asyncio
Event/Task/Future and subprocess Process waits for local guards such as `wait_for`, `asyncio.timeout`,
AnyIO/Trio timeout contexts or configured qualified `timeout_helpers`. Async
cancellation cannot bound a blocking synchronous subprocess. A known loop's
`time` replacement is reported separately because its deadlines may freeze too.
Async processes are recognized from awaited `asyncio.create_subprocess_exec` /
`create_subprocess_shell` and explicit `asyncio.subprocess.Process` annotations.
Each awaited `wait()` or `communicate()` needs its own guard: a preceding bounded
read or `kill()` does not bound the later exit wait.
External runner deadlines and general task/fixture flow are not resolved.
Bounded real-boundary polling and `asyncio.sleep(0)` are valid; positive sleeps
are not prohibited. SMT427 only suggests parametrization for narrow literal-loop
candidates and avoids common stateful replay, handoff and transaction lifecycles.
Review both heuristics in context before treating their findings as defects.

SMT428 reviews known async interactions checked only through call assertions or
asserted call history. `assert_called_once_with` can pass even when an async
operation was never awaited; use `assert_awaited_once_with` when execution matters.
Direct AsyncMock callables and explicitly async methods on known class specs are
supported, including aliases and consistent local/conftest fixture returns.
Synchronous methods, unknown specs and arbitrary children of an unspecced mock
are not assumed async. An explicit await-history check on the same mock, negative
call checks or `assert_not_awaited` for intentional scheduling preserve that intent.
Simple single-assignment locals can carry history into assertions; inherited or
arbitrarily decorated methods, transitive helpers and general data flow remain
outside scope. A finding concerns an individual test, not the suite's ability to
catch the regression. There is no automatic assertion rewrite.

SMT429 reviews functions such as `drag_gesture` that are used inside their own
module but exposed publicly only to tests. Its finding points at the definition,
suggests `_drag_gesture` and lists the test locations in JSON. Review the rename
and update internal references; prefer testing the public `drag` operation that
calls the helper. Deliberate direct helper tests can retain a reasoned exception
to SMT420 when that policy is also enabled. Functions in `__all__`, production
re-exports, decorated functions and methods are excluded. Aliased and relative
imports count as production consumers; star imports and escaped module values
conservatively preserve public visibility. External users, packaging entry points
and arbitrary dynamic access remain review limits. Checks do not rename code.

Enable SMT430 with `rules: {test-only-public-attribute: warning}` or
`--select SMT430`. It reviews public attributes such as `worker.pending` when only
tests read them outside the owning class. It shares the member-use analysis with
SMT316, SMT317 and SMT429 and suggests an available private name. Known
constructors, annotations, fixtures and class re-exports establish test receivers.
Possible production accesses preserve visibility, including unresolved same-named
receivers. Decorated/inherited classes, properties, descriptors, slots and custom
attribute hooks are excluded. Prefer observing public behavior; keep deliberate
public state with a reasoned suppression. See [the rule](docs/rules/SMT430.md) for
scope and limits.

The scope is the discovered `test_*.py`, `*_test.py` and conftest files. Test
functions use the usual test_ prefix; methods in Test-prefixed classes and direct
unittest.TestCase/IsolatedAsyncioTestCase subclasses are supported. Custom pytest
collection settings and inherited tests are not resolved. Helper modules outside
this inventory and transitive fixture/helper effects are not traversed. A fixture
in a permitted shared conftest can therefore introduce mocking into a domain test
without this path policy detecting that dependency. Direct production constructors,
explicit instance annotations and annotated local/conftest fixture returns support
SMT420. Consistent known constructor/alias fixture returns and yields are also
recognized, including fixed tuple components when unpacked. Inferred fixture
types carry separate provenance; fixture bodies are never executed. Class fixture
types stay in their own class. Private instance writes are excluded for
immutability probes. Unknown factories, conflicting fixture outputs, arbitrary
attribute types and reflection are not inferred. String target variables and f-strings are outside SMT422's
literal check. A passing static report is not evidence that tests ran or protect
their intended behavior.

Patch-count comparisons track individual syntactic calls, including multiplicity,
so accepted debt cannot hide an added or replaced patch. JSON includes policy,
operation locations and a test-quality coverage summary; `smelt context` exposes
the test policy before files are written. No automatic patch or test rewrites occur.

### Evaluate a test-quality change

Use a fixed project snapshot and compare the old/new analyzer on the same policy.
Then execute the chosen pytest boundary and, when justified, introduce a specific
plausible regression in that disposable snapshot. Confirm that the test fails for
the intended reason and still passes with the real implementation. Promote the
small failing example and an intentional valid neighbor into Smelt regressions.
Review false positives and missed cases; a lower finding count alone is no goal.

The optional maintainer runner records this comparison without installing either
version or changing policy. Replace the example paths with your snapshot, baseline
import root and selected test scope:

```bash
uv run python scripts/evaluate.py --config ../application/smelt.yml \
  --baseline ../smelt-0.1.4 --reports ../smelt-evaluation \
  --pytest-target tests/unit
```

Both import roots must contain `smelt/__init__.py`; checkouts and extracted wheels
are supported. Use `--candidate`, `--python`, `--test-python`, `--pythonpath` and
repeatable `--pytest-arg` options when the project uses a separate test environment.
`--select` defaults to SMT42; use `--select ''` for the adopted policy. Enable new
candidate-only policies in a separate review run; old versions reject unknown
configuration fields. Keep reports outside inspected source roots.

`evaluation.json` contains the configuration SHA256, optional `--source-revision`,
commands, return codes, deadlines, raw logs and added/removed/updated findings,
including multiplicity. Analysis exit 1 means findings and permits comparison.
Runner exit 0 / `complete` means the requested stages completed; without explicit
pytest targets, `pytest.executed` is false. Test failures return 1 / `tests-failed`.
Invalid analysis, malformed output, timeouts or incomplete pytest collection return
2 / `error`; invalid analysis never starts pytest. Neither status proves test
effectiveness, and this runner performs no automatic mutation or source fixes.

For complementary checks use Ruff's annotation and pytest-style rules: ANN201/
ANN202 for return annotations, ANN001 for parameters, N801 for class naming, and
PT011 for selected exception assertions missing a meaningful match/check. The
last rule can be configured for exception names that need these constraints.
Smelt does not enforce class grouping, one assertion/action, a universal ban on
control flow, assertion strength, fixture placement, duplicate test bodies,
network isolation or frozen clocks in this release. Those require separate policy
and evidence; multiple checks can legitimately describe one scenario.

### Mirrored source paths

With `tests.layout: mirror` the path of a test decides: `tests/billing/test_invoice.py`
needs `app/billing/invoice.py`, and a package test `tests/billing/test_billing.py` needs
`app/billing/`. Not every module needs a test, but a test whose source is missing or
elsewhere is an error. The message names the fix where it can: the right directory, the
right file name (`test_session_infrastructure_repository.py` should be named
`test_repository.py`), a neighbouring module with a similar name, or a missing `{root}` in
the pattern.
Imports through package facades count as imports of the module that defines the
name, so `from app.channels.application import ChannelCommands` points at
`application/commands/`. A move needs that evidence (or a test named exactly after its
subject, marked `"evidence": "name"`); a similar file name alone suggests nothing.
Otherwise the JSON lists `expected.candidates`: the imported modules, nearest first, with
the names imported from them, the facade they came through and their mirrored test path.
A test importing two modules of its name (`application/bootstrap.py` and
`infrastructure/bootstrap.py`) gets both as candidates and no move.

`tests.mirror` sets the convention relative to the test root: the default
`{path}/test_{module}.py` drops the root package, `{root}/{path}/test_{module}.py` keeps it,
and `unit/{path}/{module}_test.py` puts tests under `tests/unit/` with a suffix. In a uv
workspace each test root mirrors the package of its own member. Deliberately unmirrored
tests go in `tests.unmirrored` (e.g. `["tests/e2e/**"]`); `tests.mirror_suffixes: true`
also allows `test_invoice_<topic>.py`.

Mirroring repeats file names: `auth/presentation/test_router.py` and
`user/presentation/test_router.py`. pytest's default import mode only tells them apart
inside packages, so without `__init__.py` in the test directories it stops with "import
file mismatch". Prefixing the names (`test_auth_presentation_router.py`) works around that
but breaks the mirror; set the import mode instead, which `smelt init` points out:

```toml
[tool.pytest.ini_options]
addopts = ["--import-mode=importlib"]
```

## Adopting it in an existing project

A first check often reports dozens of findings that come down to a few decisions. Each
import finding carries an `edge` in JSON (`"billing.application -> voice.domain"`; for
cycles, the cycle), and the text output ends with the edges behind several findings.
Settle each edge once: fix the code, or record an intended dependency as policy, e.g. a
`cross_feature.allow` entry.
JSON also lists `groups`, sorted by size: a `facade` group collects the indirect
findings one module relays (its count is what narrowing that facade's exports would
address), an `edge` group counts `direct` and `transitive` findings of one dependency, a
`cycle` group carries the witness import path, and a `test_move` group the SMT401 files
moving between the same directories. Groups cite findings by their `id`; every finding
stays in `violations`, so a group is one decision, not one more defect.

Accept what remains with a baseline:

`smelt debt` records today's violations in `.smelt/debt.json` and sets `debt:` in
`smelt.yaml`. `smelt check` then fails only on new violations, and SMT903 reports entries
that were fixed and can leave the file (`smelt debt --prune`).
Existing debt files remain readable. Run `smelt debt --prune` once before editing imports
covered by an older baseline to upgrade their fingerprints; pruning accepts no new debt.
New import fingerprints survive comments, aliases and formatting changes.
Cycle debt records each cyclic architecture edge, so a new edge cannot hide behind
an existing shortest-cycle witness. Indirect import findings identify the final
forbidden target, rather than only the facade. Older cycle and indirect-import debt
may therefore reappear for review: the old fingerprints cannot establish the full
dependency they accepted. `smelt debt --prune` removes stale entries without accepting
these findings; review them before explicitly recording new debt.

Override severities by rule name or code, e.g. `rules: {unclassified-module: off}`.

Silence a single finding inline, always with a reason:

```python
from gateway.infra.sql import Repo  # smelt: ignore[SMT101] -- migration tracked in #123
```

Every rule has a page under [docs/rules](docs/rules/). `smelt init` writes a local
editor schema with completion, validation and bad/good hover examples derived
from that same catalog. See [editor setup](docs/configuration.md#editor-completion-hover-and-validation).

## Python versions

Smelt runs on Python 3.12 to 3.14 and parses your code with the Python it runs on. Code
that uses newer syntax (3.14's `except A, B:` or t-strings, 3.13's type parameter defaults)
needs smelt on that version, e.g. `uvx -p 3.14 --from smelt-cli smelt check`; the syntax error says so when
`requires-python` or `.python-version` targets a newer Python.

## Using Smelt with coding agents

Add this to your `AGENTS.md` or `CLAUDE.md`:

```md
Before implementing, run `smelt context <feature>` to see where code belongs.
After every change, run `smelt check --changed --strict-coverage --format json`.
Do not finish while errors remain. Use `smelt explain <code>` when unsure.
Resolve placement problems; do not relax policy or record debt just to make the check pass.
```

Suggested loop: `smelt context <feature>` → edit → `smelt check --changed` → fix → tests →
pre-commit → CI (full check). Prefer the cheapest verification that gives sufficient
confidence: a rename needs smelt plus a type checker, new behavior needs one focused
regression test.

## pre-commit

```yaml
repos:
  - repo: https://github.com/mathisarends/smelt
    rev: v0.1.6
    hooks:
      - id: smelt
```

The hook checks the whole project when Python files or `smelt.yaml` / `smelt.yml`
change. Checking only changed filenames can miss violations in modules that import them.

## GitHub Actions

CI checks the whole repository; `--changed` is for the agent loop.

```yaml
- uses: astral-sh/setup-uv@v6
- run: uvx --from smelt-cli smelt check --format github
# optional: code scanning
- run: uvx --from smelt-cli smelt check --format sarif > smelt.sarif || true
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: smelt.sarif
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rule extension workflow, code layout,
regression requirements and PR checklist. `smelt rules --format json` lists each
rule's analysis requirements and configuration paths; `smelt explain <code>` gives
the examples and rationale.

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # create .venv and install dev dependencies
uv run pre-commit install
```

Common commands:

```bash
uv run pytest                        # run tests
uv run pytest --cov                  # run tests with coverage
uv run ruff check --fix .            # lint
uv run ruff format .                 # format
uv run mypy                          # type-check
uv run pre-commit run --all-files    # run all hooks
uv run smelt check                   # smelt checks itself
uv run python scripts/generate.py    # refresh smelt.schema.json and docs/rules/
```

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/).

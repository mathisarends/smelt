# Level 6: Scaling the policy

The earlier levels described one project with layers, features and tests. This
guide covers what larger codebases need: package APIs that hide internals,
indirect dependencies, shared source conventions, policy shared between
projects and uv workspaces with several members.

## Package APIs

Layers say which parts may import each other. A package API says *through
which modules*. Declare the public entry points of a package, and callers
outside it may import only those:

```yaml
architecture:
  packages:
    feature-shape:
      match: app.features.*          # one package segment
      required: [application.api, domain]
      public: [application.api]
    toolkit:
      match: toolkit
      public: ['.']                  # only the package facade

rules:
  architecture/non-public-module-import: error   # ARC007
  architecture/missing-required-module: error    # ARC014
```

```python
# app/client.py
from app.orders.storage import OrderStore   # ARC007: private module of a contracted package
from app.orders.api import place_order      # fine: a public entry point
```

ARC014 reports features that lack a required module, so every feature keeps the
same shape. Tests count as outside callers; `test_access: mirrored` lets the
mirrored tests of a package import its internals. See
[package APIs and source conventions](structure.md) for public patterns,
bundling thresholds and facade exports.

## Indirect dependencies

By default, boundary rules check each module's own imports. A helper can hide a
forbidden dependency one hop away:

```text
app.domain.model → app.shared.utils → sqlalchemy
```

Turn on transitive checking to apply every layer, feature and third-party policy
to all reachable modules:

```yaml
architecture:
  import_graph:
    transitive: true
```

Findings point at the originating import and include the shortest chain. This
is a module-graph check: importing one name from a facade makes all of that
facade's imports reachable, so expect to narrow facades or widen
`may_depend_on` when you first enable it. See
[architecture as code](architecture.md) for the details.

## Source conventions

Rules beyond architecture and tests cover modeling, typing, idioms, imports,
complexity and visibility. Some take options, set once under `style` and
overridden for matching packages:

```yaml
style:
  dataclass_options: [slots]
  overrides:
    legacy:
      match: app.legacy
      dataclass_options: []

rules:
  modeling/missing-dataclass-option: warning
  complexity/excess-positional-parameters: {severity: warning, max: 3}
```

Options configure rules but never enable them; an option without its rule is a
configuration warning. Browse the [rule catalog](rules/README.md) or run
`smelt rules --unconfigured` for candidates, and see
[project-wide options](structure.md#project-wide-options-and-package-overrides)
for every option and its default.

## Exceptions for a part of the codebase

Silence a rule for whole paths with a reason, instead of one comment per line:

```yaml
ignore:
  - rule: tests/excess-patches
    paths: ["tests/legacy/**"]
    reason: Migrating the legacy test suite.
```

## Shared policy across projects

Independent projects in one repository can share explicit policy through a
local `extends` file:

```yaml
# smelt.base.yml
version: 2
rules:
  architecture/import-cycle: error
  tests/unbounded-wait: warning
```

```yaml
# runtime/smelt.yml
extends: ../smelt.base.yml
project:
  source_roots: [src]
  test_roots: [tests]
rules:
  tests/unbounded-wait: error   # a later named setting replaces the base
```

Mappings merge, lists replace, and each file is its own rules layer. Use
`smelt config show --sources` to see where every effective value came from. See
[shared local policy](configuration.md#shared-local-policy).

## uv workspaces

A uv workspace is checked as one project with one import graph. The root config
declares the workspace, shared rules and member boundaries:

```yaml
# smelt.yml at the workspace root
version: 2
project:
  workspace: uv
  test_roots: [tests]
architecture:
  members:
    "services/*": {may_depend_on: ["packages/*"]}
    "packages/*": {may_depend_on: []}
rules:
  architecture/member-boundary: error   # ARC009
  tests/missing-outcome-check: warning
```

Each member can keep its own architecture and reasoned exceptions in its own
`smelt.yml`. Lowering an inherited severity needs a reason:

```yaml
# packages/legacy/smelt.yml
rules:
  complexity: {severity: off, reason: Migrating the legacy service.}
```

`smelt init` drafts the root and every member; `smelt debt` keeps one baseline
per member. See [uv workspaces](configuration.md#uv-workspaces) and
[workspace members](rule-selection.md#workspace-members).

## Where to go from here

You now know every layer of a Smelt policy. The reference guides cover each one
completely:

- [Configuration](configuration.md): every key, inheritance and editor support.
- [Rule activation](rule-selection.md): selectors, severities and precedence.
- [CLI and reports](cli.md): every command and the JSON report schema.
- [Rule catalog](rules/README.md): every rule with its rationale and examples.

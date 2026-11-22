# Level 1: First check

This guide takes you from a fresh install to a project policy with three rules,
and shows what to do with each finding. The running example is a small shop:

```text
src/shop/
  domain/orders.py
  application/checkout.py
  infrastructure/database.py
tests/
  test_checkout.py
```

## Install

```bash
uv tool install smelt-cli
smelt --version
```

Smelt runs on Python 3.12 to 3.14 and parses your code with the Python it runs
on. Code that uses newer syntax needs Smelt on that version, for example
`uvx -p 3.14 --from smelt-cli smelt check`.

## Draft a policy

```bash
smelt init
```

```text
Wrote smelt.yml
  Wrote .smelt.schema.json for editor completion, validation and hover examples.
  root packages: shop
  layers: domain (domain), application (application), infrastructure (infrastructure)
  tests: no test mirrors a module yet, mirroring is off
The inferred config yields 1 error and 0 warnings.
```

`init` reads your package layout and writes a commented `smelt.yml` next to an
editor schema. It never overwrites an existing config without `--force`. Stripped
of comments, the draft for the shop looks like this:

```yaml
version: 2

project:
  source_roots: [src]
  test_roots: [tests]

architecture:
  layers:
    domain: {path: domain, may_depend_on: [], third_party: allow}
    application: {path: application, may_depend_on: [domain], third_party: allow}
    infrastructure:
      path: infrastructure
      may_depend_on: [domain, application]
      third_party: allow

tests:
  layout: none

rules:
  architecture/layer-boundary: error
```

Two kinds of keys matter here:

- **Policies** such as `architecture.layers` and `tests.layout` describe your
  project: which layers exist and what they may import.
- **`rules`** decide which checks run and how severe a finding is. A rule runs
  only when `rules` names it. Declaring layers without enabling
  `architecture/layer-boundary` checks nothing, and Smelt warns about the
  unchecked policy.

> [!TIP]
> `smelt init --maintainability` additionally writes a selection of source and
> test reviews, each by its individual name, with optional hints commented out.

## Run the check

```bash
smelt check
```

```text
src/shop/application/checkout.py:2:42  ARC001 architecture/layer-boundary  [error]
  application must not depend on infrastructure
    from shop.infrastructure.database import SqlOrderRepository
                                             ^^^^^^^^^^^^^^^^^^
  Allowed: application → domain
  Hint: Move what application needs into a layer it may depend on (domain), or invert
        the dependency behind an abstraction.

Policy coverage: complete (configured); 0 uncovered, 0 unclassified sources
✗ 1 error · 0 warnings · 0 hints · 7 modules
```

Every finding has the same parts:

| Part | Example | Meaning |
| --- | --- | --- |
| Location | `checkout.py:2:42` | File, line and column |
| Code and name | `ARC001 architecture/layer-boundary` | A stable code and a `family/finding` name; both work everywhere |
| Severity | `[error]` | `error`, `warning` or `hint`, set in `rules` |
| Evidence | `Allowed: application → domain` | Why this line violates the policy |
| Hint | `Move what application needs…` | The repair |

By default `smelt check` fails on errors only. Use `--fail-on warning` to fail on
warnings too, `--show-hints` to print hints, and `--format concise` for one line
per finding.

## Understand a rule

```bash
smelt explain ARC001
```

`explain` prints how to enable the rule, its options, its status in your
project, the reasoning behind it, a bad and a good example and how to fix it.
Each rule also has a generated page in the [rule catalog](rules/README.md).

## Add rules

Adding a rule is a one-line policy decision. Enable a typing review and the
mirrored test layout:

```yaml
tests:
  layout: mirror

rules:
  architecture/layer-boundary: error
  typing/implicit-optional-default: warning
  tests/misplaced-test: error
```

```text
src/shop/application/checkout.py:10:12  TYP003 typing/implicit-optional-default  [warning] [unsafe fix]
  optional parameter 'limit' uses truthiness for its default
    return limit or 100
           ^^^^^^^^^^^^
  Hint: Use an explicit is not None conditional if only None should select the fallback;
        review intentional falsy defaults.

tests/test_checkout.py  TST001 tests/misplaced-test  [error]
  test_checkout.py belongs in tests/application/
  Expected: tests/application/test_checkout.py
```

The value of each entry is a severity (`error`, `warning`, `hint`), `on` for the
rule's catalog severity, or `off`. Families such as `typing` and `ALL` can turn
already-enabled rules off or cap their severity, but never enable anything. To
find rules you have not decided on yet:

```bash
smelt rules --unconfigured
smelt check --select typing   # try a family for one run without changing smelt.yml
```

## Act on a finding

Each finding leaves you three honest options.

**Fix the code.** Move the test to `tests/application/test_checkout.py`. For
TYP003, Smelt can apply the repair after you review it:

```bash
smelt check --select TYP003 --diff --unsafe-fixes   # preview
smelt check --select TYP003 --fix --unsafe-fixes    # apply
```

```python
def page_size(limit: int | None = None) -> int:
    return (limit if limit is not None else (100))
```

`--fix` applies only safe fixes; `--unsafe-fixes` opts into fixes such as this
one that can change behavior.

**Keep it deliberately.** If treating `0` as "use the default" is intended, say
so where the code is:

```python
return limit or 100  # smelt: ignore[TYP003] -- Zero means use the default page size.
```

Suppressions name the rule and carry a reason, so a reviewer can judge the
exception.

**Change the policy.** If the finding shows a decision you disagree with, edit
`smelt.yml`: allow the dependency, lower the severity or set the rule `off`.
Policy edits show up in the diff like any other code change.

> [!IMPORTANT]
> Do not disable rules or suppress findings just to get a green check. A
> suppression without a real reason hides the same decision the next reviewer
> will have to make again.

## Next

[Level 2: Architecture boundaries](02-architecture-boundaries.md) explains how
to describe layers, features and shared code, and how to fix the ARC001 finding
above properly.

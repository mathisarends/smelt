# Level 1: First check

Write the first entries of your style guide and act on what Smelt finds. The
example is a small shop:

```text
src/shop/{domain,application,infrastructure}/
tests/test_checkout.py
```

## Draft the guide

```bash
uv tool install smelt-cli
smelt init
```

`init` infers layers from your layout and writes `smelt.yml` plus an editor
schema. Without comments:

```yaml
version: 2

project:
  source_roots: [src]
  test_roots: [tests]

architecture:
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}

rules:
  architecture/layer-boundary: error
```

`architecture` describes the project. `rules` decides what is enforced: a rule
runs only when it is listed here. `smelt init --maintainability` adds a starter
set of source and test rules.

## Add conventions

Each convention is one line:

```yaml
tests:
  layout: mirror

rules:
  architecture/layer-boundary: error
  typing/implicit-optional-default: warning
  tests/misplaced-test: error
```

The value is `error`, `warning`, `hint`, `on` (catalog severity) or `off`. Find
more with `smelt rules --unconfigured`, or try a family for one run with
`smelt check --select typing`.

## Read the findings

```bash
smelt check
```

```text
src/shop/application/checkout.py:2:42  ARC001 architecture/layer-boundary  [error]
  application must not depend on infrastructure
  Allowed: application → domain

src/shop/application/checkout.py:10:12  TYP003 typing/implicit-optional-default  [warning]
  optional parameter 'limit' uses truthiness for its default
    return limit or 100

tests/test_checkout.py  TST001 tests/misplaced-test  [error]
  Expected: tests/application/test_checkout.py

✗ 2 errors · 1 warning · 0 hints · 7 modules
```

Each finding has a location, a stable code (`TYP003`), a name
(`typing/implicit-optional-default`), evidence and a repair hint. `smelt check`
fails on errors; add `--fail-on warning` to fail on warnings too.
`smelt explain TYP003` prints the reasoning and a good and a bad example.

## Act on a finding

| Choice | How |
| --- | --- |
| Fix the code | Move the test; for TYP003, `smelt check --select TYP003 --fix --unsafe-fixes` |
| Keep a deliberate exception | `return limit or 100  # smelt: ignore[TYP003] -- Zero means the default.` |
| Change the convention | Edit `smelt.yml`; the change shows up in review |

Never weaken a rule just to get a green check.

**Next:** [Level 2: Architecture boundaries](02-architecture-boundaries.md)

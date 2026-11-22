# smelt

**Turn recurring review comments into checks your coding agents run themselves.**

Coding agents write code that passes the tests and the type checker and still
erodes the structure your team agreed on. An application service imports the
database. A test lands where nobody will look for it. A helper becomes public
API by accident. `limit or 100` quietly throws away a valid `0`. Reviewers write
the same comment for the tenth time, and the next agent session repeats the
mistake, because the decision only lives in someone's head or an old PR thread.

Smelt makes those decisions explicit. You write them down once in `smelt.yml`;
Smelt checks them statically before an agent edits, after every edit, at commit
time and in CI. Each finding names the rule, explains why it matters and says how
to fix it, so the agent repairs it before a human reviews the PR.

**Without Smelt:** a review comment, after the PR is open.

```text
> Please don't import the SQL repository from the application layer.
> Depend on a port and wire the repository in bootstrap.py.
```

**With Smelt:** a finding, before the commit exists.

```text
src/shop/application/checkout.py:2:42  ARC001 architecture/layer-boundary  [error]
  application must not depend on infrastructure
    from shop.infrastructure.database import SqlOrderRepository
                                             ^^^^^^^^^^^^^^^^^^
  Allowed: application → domain
  Hint: Move what application needs into a layer it may depend on (domain), or invert
        the dependency behind an abstraction.
```

## Why Smelt?

- **Your policy, written down.** Rules only run when `smelt.yml` names them, so
  the file is the complete, reviewable list of your conventions. New rules in a
  Smelt release stay off until you name them.
- **Architecture first.** Layers, features, shared code, composition roots,
  third-party dependencies and tests that mirror the source tree are first-class
  policy, not regex lints.
- **Built for agent loops.** `smelt context` tells an agent what applies *before*
  it writes a file; `--changed` and `--staged` checks report only what the edit
  introduced; a bundled skill gives agents the full reference offline.
- **Adoptable today.** A debt baseline accepts existing findings so a legacy
  codebase fails only on new violations.
- **Fast enough to run between edits.** Checks reuse a worker and cache analysis
  by content; an unchanged staged retry took about 50 ms in the
  [agent workflow benchmark](docs/agent-workflow.md#native-git-retained-worker-state-and-bounded-failure-handling).

## Installation

```bash
uv tool install smelt-cli
```

Requires Python 3.12+. The command is `smelt`; release wheels include the native
client.

Upgrading to 0.15.0? Follow the [upgrade guide](docs/upgrading.md#014-to-015)
for configuration changes, inline suppressions and hook pins.

## Quick start

For a project with `src/shop/{domain,application,infrastructure}` and `tests/`:

```bash
smelt init     # infer layers from your layout and write smelt.yml
smelt check    # check the project against it
```

`smelt init` proposes a starting policy. Review it: you decide which conventions
to adopt. This one encodes three review comments: application code must not
depend on infrastructure, `None` defaults must not swallow valid falsy values,
and tests mirror the source tree.

```yaml
# smelt.yml
version: 2

project:
  source_roots: [src]
  test_roots: [tests]

architecture:
  layers:
    domain: {path: domain, may_depend_on: []}
    application: {path: application, may_depend_on: [domain]}
    infrastructure: {path: infrastructure, may_depend_on: [domain, application]}

tests:
  layout: mirror

rules:
  architecture/layer-boundary: error
  typing/implicit-optional-default: warning
  tests/misplaced-test: error
```

```text
src/shop/application/checkout.py:2:42  ARC001 architecture/layer-boundary  [error]
src/shop/application/checkout.py:10:12  TYP003 typing/implicit-optional-default  [warning]
tests/test_checkout.py  TST001 tests/misplaced-test  [error]
✗ 2 errors · 1 warning · 0 hints · 7 modules
```

Each finding also shows the offending code, the evidence and a repair hint, as
in the example above.

Fix the code, record a deliberate exception with a reason, or accept today's
findings as debt and fail only on new ones.

## Core commands at a glance

| You want to… | Command |
| --- | --- |
| Draft a policy from your layout | `smelt init` (add `--maintainability` for source and test reviews) |
| Check the project | `smelt check --fail-on warning` |
| Check only what you just changed | `smelt check --changed` (`--base origin/main` for a branch) |
| Check the commit you are about to make | `smelt check --staged --fail-on warning` |
| See what applies before writing a file | `smelt context src/shop/application/refunds.py --guide` |
| Understand a finding | `smelt explain ARC001` |
| Find rules you have not decided on | `smelt rules --unconfigured` |
| Adopt Smelt in an existing codebase | `smelt debt` |
| Give agents the offline reference | `smelt skill install` |

## Catch it at commit time

Smelt uses the pre-commit hook format. With [prek](https://prek.j178.dev/installation/),
add this to `.pre-commit-config.yaml` and run `prek install`:

```yaml
repos:
  - repo: local
    hooks:
      - id: smelt-staged
        name: smelt
        entry: smelt check --staged --fail-on warning
        language: python
        additional_dependencies: [smelt-cli==0.15.0]
        pass_filenames: false
        always_run: true
        require_serial: true
```

The hook checks the Git index, including partial staging, deletions and policy
changes, and blocks new warnings and errors. Run `smelt check --fail-on warning`
in CI for the full project. See [integrations](docs/integrations.md) for GitHub
Actions and other installation options.

## Documentation

The docs are a learning path: each level builds on the previous one.

| Level | Guide | You learn |
| --- | --- | --- |
| 1 | [First check](docs/01-first-check.md) | Install, `init`, reading findings, fixing or suppressing them |
| 2 | [Architecture boundaries](docs/02-architecture-boundaries.md) | Layers, features, shared code, composition roots, third-party imports |
| 3 | [Tests that mirror the source](docs/03-test-layout.md) | Mirrored test layout and opt-in test quality checks |
| 4 | [The agent loop](docs/04-agent-loop.md) | `context`, changed and staged checks, the skill, hooks and CI |
| 5 | [Existing codebases](docs/05-existing-codebases.md) | Grouped findings, debt baselines, strict policy coverage |
| 6 | [Scaling the policy](docs/06-scaling-the-policy.md) | Package APIs, transitive imports, shared configs, uv workspaces |

The [documentation index](docs/README.md) lists the reference guides behind
each level: [configuration](docs/configuration.md), [rule activation](docs/rule-selection.md),
the [rule catalog](docs/rules/README.md) and the [CLI and reports](docs/cli.md).
See also the [upgrade guide](docs/upgrading.md), the [changelog](CHANGELOG.md)
and [contributing](CONTRIBUTING.md).

## Scope and limits

Smelt analyzes Python source statically and never executes your code. Use it
alongside tests, a type checker and Ruff; advisory findings still need review.
See [scope and limits](docs/vision.md). Run Smelt with a Python version that can
parse your code. Building from source requires Rust 1.88+ and a platform linker.

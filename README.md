# smelt

Smelt is a static checker for Python codebases maintained by people and coding
agents. Describe your architecture and adopt source and test conventions in
`smelt.yaml`; Smelt turns those decisions into actionable checks that can run
locally, in pre-commit and in CI.

Use it both as **architecture as code** and as an executable style guide:
agents can inspect the policy before editing, check their changes, and iterate
on findings before the same project-wide checks run in CI.

## What Smelt supports

| Area | Examples | Learn more |
| --- | --- | --- |
| Architecture | Layer and feature boundaries, third-party dependencies, cycles, composition roots, wiring, and coverage of production code by policy | [Architecture](docs/architecture.md) |
| Package contracts | Required modules and public entry points; checks for imports of private implementation modules | [Package contracts](docs/structure.md) |
| Source conventions | Package-scoped import styles, module names and headers, public-first ordering, internal helpers, and selected readability checks | [Source conventions](docs/structure.md) |
| Test conventions | Mirrored test paths and opt-in reviews of outcomes, mocks, private access, async awaits, waits, and test-only public members | [Test policies](docs/testing.md) |
| Agent and CI workflows | Policy briefings, staged and changed checks, JSON, GitHub annotations, SARIF, pre-commit and debt baselines | [Integrations](docs/integrations.md), [agent workflow](docs/agent-workflow.md), [CLI and reports](docs/cli.md), [adoption](docs/adoption.md) |

The [rule catalog](docs/rules/README.md) lists each check, its activation and
default severity. Most rules are opt-in; a declared architecture, package or
test policy activates the corresponding checks. See [rule activation](docs/rule-selection.md).

## Quick start

Smelt requires Python 3.12 or newer. Install the `smelt-cli` package (the
unrelated PyPI package `smelt` is not this project):

```bash
uv tool install smelt-cli
# or: pip install smelt-cli
```

From your project root:

```bash
smelt init --maintainability  # draft smelt.yaml and select source/test checks
smelt check --show-hints --fail-on warning
smelt context <feature-or-path>  # inspect the policy before making a change
smelt explain SMT101             # understand a finding and its configuration
```

Review the generated configuration before adopting it: inference proposes a
starting point, including architecture when recognizable; it does not decide
your intended design. Small libraries can use named checks without features or
layers. See [configuration](docs/configuration.md) and [adoption](docs/adoption.md).

To run without installing, use `uvx --from smelt-cli smelt check`. From a local
checkout, use `uv tool install .`. Run Smelt with a Python version that can parse
the project's syntax; see [Python versions](docs/integrations.md#python-versions).
Building from a checkout or source distribution requires Rust 1.88+ and a platform
linker. Release wheels include the native client.

## Agent loop and enforcement

1. Inspect the policy with `smelt context <feature-or-path>`.
2. Edit, then run `smelt check --changed --format json` and address findings.
3. Run focused tests, stage the intended changes, and run
   `smelt check --staged --fail-on warning` and `prek run --all-files`.
   Inspect hook fixes, stage the intended result, and rerun until checks pass.
4. Run a full `smelt check` in CI to enforce the adopted policy project-wide.

For strict architecture coverage, add `--strict-coverage`; it requires every
discovered production file to have a role or a reasoned exclusion. Changed checks
compare against `HEAD` by default, including uncommitted changes. For PRs,
`--changed --base origin/main` compares against the merge base. The
[integration guide](docs/integrations.md) includes a reusable agent instruction,
pre-commit configuration, and GitHub Actions example. The [agent workflow](docs/agent-workflow.md)
explains staged checks, partial staging and caching.

## Scope

Smelt analyzes Python source statically. It checks the policies and rule patterns
it supports, not arbitrary runtime behavior, dynamic imports, or test effectiveness.
Many review-oriented checks are advisory and require judgment; a clean report
does not establish correctness. Use tests, type checking, and tools such as Ruff
alongside Smelt. See the [vision and scope](docs/vision.md) and each rule's page for
its precise limits.

## Documentation and development

Start with the [documentation index](docs/README.md), or browse the
[configuration reference](docs/configuration.md), [rule catalog](docs/rules/README.md),
and [changelog](CHANGELOG.md). For development setup, checks, rule contributions,
and releases, see [CONTRIBUTING.md](CONTRIBUTING.md).

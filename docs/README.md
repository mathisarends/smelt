# Smelt documentation

Smelt is a programmatically enforced style guide for Python: every convention
in `smelt.yml` is a check that runs on each edit, commit and CI run. Start with
the [README](../README.md).

## Learning path

| Level | Guide | You can… |
| --- | --- | --- |
| 1 | [First check](01-first-check.md) | write the first rules and act on findings |
| 2 | [Architecture boundaries](02-architecture-boundaries.md) | enforce layers, features and third-party imports |
| 3 | [Tests that mirror the source](03-test-layout.md) | enforce test layout and test conventions |
| 4 | [The agent loop](04-agent-loop.md) | have agents check their own work before a commit |
| 5 | [Existing codebases](05-existing-codebases.md) | adopt Smelt with a debt baseline |
| 6 | [Scaling the policy](06-scaling-the-policy.md) | add package APIs, shared configs and workspaces |

## Reference

| Topic | Guide |
| --- | --- |
| Every configuration key | [Configuration](configuration.md) |
| Enabling and selecting rules | [Rule activation](rule-selection.md) |
| Every rule | [Rule catalog](rules/README.md) (generated) |
| Layers, features, coverage | [Architecture as code](architecture.md) |
| Package APIs, code conventions | [Package APIs and source conventions](structure.md) |
| Test rules | [Test policies](testing.md) |
| Commands and report formats | [CLI and reports](cli.md) |
| Hooks and CI | [Agents, hooks and CI](integrations.md) |
| Debt baselines | [Adoption and debt](adoption.md) |
| Migration | [Upgrading](upgrading.md), [changelog](../CHANGELOG.md) |

## Project

[Vision](vision.md) · [Contributing](../CONTRIBUTING.md) ·
[Agent workflow and benchmarks](agent-workflow.md) · [Review backlog](review.md)

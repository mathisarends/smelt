# Smelt documentation

Smelt turns recurring review comments into checks that coding agents run
themselves. You write architecture and code conventions down once in
`smelt.yml`; Smelt checks them before an edit, after it, at commit time and in
CI, and explains every finding.

Start with the [project overview and quick start](../README.md).

## Learning path

Each level builds on the one before it and extends the same example project.

| Level | Guide | After reading it you can… |
| --- | --- | --- |
| 1 | [First check](01-first-check.md) | draft a policy, read findings and fix, suppress or accept them deliberately |
| 2 | [Architecture boundaries](02-architecture-boundaries.md) | describe layers, features, shared code and the composition root, and restrict third-party imports |
| 3 | [Tests that mirror the source](03-test-layout.md) | keep the test tree predictable and enable the test reviews your team makes |
| 4 | [The agent loop](04-agent-loop.md) | have agents read the policy before an edit, check their changes and get blocked at commit time |
| 5 | [Existing codebases](05-existing-codebases.md) | adopt Smelt in a legacy project with a debt baseline and enforce complete policy coverage |
| 6 | [Scaling the policy](06-scaling-the-policy.md) | define package APIs, check indirect imports and share policy across projects and uv workspaces |

## Reference

The learning path shows each concept once. The reference guides cover them
completely:

| If you want to… | Read |
| --- | --- |
| Look up every configuration key, inheritance and editor support | [Configuration](configuration.md) |
| Understand how rules are enabled, selected and layered | [Rule activation](rule-selection.md) |
| Look up a rule's rationale, options and examples | [Rule catalog](rules/README.md) |
| Describe boundaries, features, layers or strict policy coverage | [Architecture as code](architecture.md) |
| Define package APIs and source conventions | [Package APIs and source conventions](structure.md) |
| Configure test layout and test quality checks | [Test policies](testing.md) |
| Use every command and the JSON, SARIF and GitHub report formats | [CLI and reports](cli.md) |
| Set up hooks, CI and agent instructions | [Agents, hooks and CI](integrations.md) |
| Manage debt baselines in detail | [Adoption and debt](adoption.md) |
| Migrate from an earlier release | [Upgrading Smelt](upgrading.md) and the [changelog](../CHANGELOG.md) |

The rule catalog and the pages in `rules/` are generated from the rule metadata.

## Project and contributing

- [Vision](vision.md): what Smelt is for, what good tests look like and the
  scope brake for new rules.
- [Contributing](../CONTRIBUTING.md): architecture, rule naming, testing and
  releases.
- [Agent workflow](agent-workflow.md): caching, staged checks and performance
  measurements.
- [Review backlog](review.md): known gaps and next priorities.

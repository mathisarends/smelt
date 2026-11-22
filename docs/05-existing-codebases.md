# Level 5: Existing codebases

On a greenfield project, every finding is a fresh decision. On a codebase that
grew for years, the first `smelt check` can report hundreds. This guide adopts
Smelt there without a big-bang cleanup: settle the decisions behind the
findings, accept the rest as debt and fail only on new violations.

## 1. See the shape before the detail

```bash
smelt init                       # infer architecture from the existing layout
smelt check --statistics         # counts per rule instead of every finding
```

Start with few rules. Each additional rule is a decision you can make later
with `smelt rules --unconfigured`. To try a rule without committing to it, run
it for one check:

```bash
smelt check --select architecture/cross-feature-import --format concise
```

## 2. Settle decisions, not findings

Dozens of import findings usually come from a handful of dependencies. The text
output ends with the edges behind several findings, and every import finding in
JSON carries an `edge` such as `"billing.application -> voice.domain"`:

```bash
smelt check --format json
```

The JSON `groups` list, sorted by size, bundles findings that share one
decision: an `edge` group for one dependency, a `cycle` group for one import
cycle, a `facade` group for findings one package facade relays and a
`test_move` group for TST001 files moving between the same directories.

Settle each group once. Either fix the code, or record the dependency as
intended policy, for example a `cross_feature.allow` entry. A policy edit that
allows a dependency is visible in review; dozens of suppressions are not.

## 3. Accept the rest as debt

```bash
smelt debt
```

```text
Wrote .smelt/debt.json (3 violations, 3 matching entries)
Added `debt: .smelt/debt.json` to smelt.yml
```

```text
✓ architecture ✓ typing ✓ tests · 3 in debt · 7 modules
```

The baseline records today's findings by stable fingerprints that survive
comments, aliases, formatting and line shifts. From now on `smelt check` fails
only on new violations. An old finding moved to a new function, or a new
dependency replacing an accepted one, is still new.

As debt gets fixed, the baseline shrinks:

```bash
smelt check          # MET003 reports entries that were fixed
smelt debt --prune   # remove them; pruning never accepts new findings
smelt debt --show src/shop/application   # inspect what is still accepted
```

> [!IMPORTANT]
> Debt is for the past. Record it deliberately when adopting a rule, never to
> get a failing change through. The agent instructions from
> [level 4](04-agent-loop.md#2-tell-the-agent-the-loop) say so explicitly.

## 4. Check only new work

With debt in place, the loop from level 4 works unchanged: `--changed` and
`--staged` report what an edit introduced, and the full check in CI fails only
on findings outside the baseline. Enable `tests/missing-mirror-test` and record
the current gaps, and new modules without a test are reported from then on.

## 5. Leave nothing unplaced: strict coverage

A module without an architecture role escapes every boundary check. Once the
policy describes your project, close that gap:

```yaml
rules:
  architecture/uncovered-source: error   # ARC013
```

or, for one run, `smelt check --strict-coverage`. Every discovered production
file must then be governed by a layer, a feature, shared code, wiring or the
composition root, be a package facade, or be excluded with a reason:

```yaml
project:
  exclude:
    - path: "src/shop/generated/**"
      reason: "Generated client; regeneration is checked separately."
```

ARC013 is a guard: it always reports errors, also in `--changed` and `--select`
runs, and debt or suppressions cannot hide it. Use
`smelt check --show-coverage --format json` for the per-file inventory and
`smelt context <path>` to see why a file is uncovered.

## Further reading

- [Adoption and debt](adoption.md): workspace baselines, the grouped debt format
  and fingerprint upgrades.
- [Strict policy coverage](architecture.md#strict-policy-coverage): every
  coverage status and what `coverage.complete` means.

## Next

[Level 6: Scaling the policy](06-scaling-the-policy.md) covers package APIs,
transitive imports, shared configuration and uv workspaces.

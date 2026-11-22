# Level 5: Existing codebases

Introduce a style guide to a codebase that already breaks it, without a
big-bang cleanup.

## 1. Start small

```bash
smelt init
smelt check --statistics   # counts per rule
```

Enable few rules first; add more later with `smelt rules --unconfigured`.

## 2. Decide edges, not findings

Many import findings come from a few dependencies. The text report ends with the
edges behind them, and `--format json` groups findings by `edge`, `cycle`,
`facade` and `test_move`. Settle each group once: fix the code or allow the
dependency in `smelt.yml`.

## 3. Accept the rest as debt

```bash
smelt debt            # writes .smelt/debt.json and sets debt: in smelt.yml
smelt check           # now fails only on new violations
smelt debt --prune    # drop entries that were fixed (MET003 reports them)
```

Fingerprints survive formatting and line shifts. Debt is for the past, never
for getting a new change through.

## 4. Leave nothing unplaced

```yaml
rules:
  architecture/uncovered-source: error   # ARC013, or: smelt check --strict-coverage
```

Every production file must then have an architecture role or a reasoned
exclusion:

```yaml
project:
  exclude:
    - {path: "src/shop/generated/**", reason: "Generated client."}
```

ARC013 cannot be suppressed or recorded as debt. Details:
[adoption and debt](adoption.md), [strict coverage](architecture.md#strict-policy-coverage).

**Next:** [Level 6: Scaling the policy](06-scaling-the-policy.md)

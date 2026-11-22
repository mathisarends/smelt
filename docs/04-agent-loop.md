# Level 4: The agent loop

A style guide only helps if agents follow it while they work. Smelt gives them
a check at each step:

| When | Command |
| --- | --- |
| Before writing a file | `smelt context <path> --guide` |
| After an edit | `smelt check --changed --format concise` |
| When stuck | `smelt explain <code>` |
| At commit | the staged prek hook |
| In CI | `smelt check --fail-on warning` |

## 1. Install the skill

```bash
smelt skill install   # .agents/skills/smelt, the offline reference for this release
```

After upgrading Smelt, run `smelt skill install --force`.

## 2. Add to `AGENTS.md` or `CLAUDE.md`

```md
Before editing, run `smelt context <feature-or-path>` to read the applicable policy
and `smelt context <path> --guide` for the instructions to write the file by.
After editing, run `smelt check --changed --format concise` and address new findings.
Use `smelt explain <code>` for the rule's intent and repair advice.
Run focused tests, stage your changes, then run `prek run --all-files`.
Inspect and stage any hook fixes, and rerun until the checks pass.
Do not disable rules or record debt just to pass a check.
```

## 3. Before: the rules for one file

```bash
smelt context src/shop/application/refunds.py --guide
```

```text
Layer: application
Required:
  ARC001  From layer application, import only domain; reach other layers through a port
Expected:
  TYP003  Check optional parameters with is not None, not truthiness, before falling back
Tests (tests/application/test_refunds.py)
```

Works for files and features that don't exist yet.

## 4. After: only what the edit introduced

`smelt check --changed` compares with `HEAD`; use `--base origin/main` for a
branch. It still analyzes the whole project, so a broken caller is reported.

## 5. Commit and CI

```yaml
# .pre-commit-config.yaml
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

```yaml
# GitHub Actions
- uses: astral-sh/setup-uv@v7
- run: uvx --from smelt-cli==0.15.0 smelt check --format github
```

The hook checks the Git index and blocks new findings. Checks reuse a worker
and a content cache; an unchanged retry takes tens of milliseconds
([benchmark](agent-workflow.md)). More options: [integrations](integrations.md).

**Next:** [Level 5: Existing codebases](05-existing-codebases.md)

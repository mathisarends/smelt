# Level 4: The agent loop

Levels 1 to 3 built a policy. This guide puts it where it pays off: in the loop
a coding agent runs while it works. The agent reads the policy before an edit,
checks its own change after the edit and cannot commit a new finding.

```text
smelt context <path> --guide     before: what applies to the file I'm about to write?
smelt check --changed            after:  what did my edit introduce?
smelt explain <code>             stuck:  why does this rule exist, how do I fix it?
smelt check --staged             commit: the prek hook blocks new findings
smelt check                      CI:     the full project
```

## 1. Install the skill

```bash
smelt skill install
```

The skill gives agents the complete rule, configuration and command reference
offline, matching the installed release. It installs in `.agents/skills/smelt`;
use `--destination <skill-directory>` for another location. After upgrading
Smelt, run `smelt skill install --force` to replace the old version. Replacement
discards local edits, so keep project-specific instructions in `AGENTS.md` or
`CLAUDE.md`. See [agent skill](cli.md#agent-skill).

## 2. Tell the agent the loop

Add this to your `AGENTS.md` or `CLAUDE.md`:

```md
Before editing, run `smelt context <feature-or-path>` to read the applicable policy
and `smelt context <path> --guide` for the instructions to write the file by.
After editing, run `smelt check --changed --format concise` and address new findings.
Use `smelt explain <code>` for the rule's intent and repair advice.
Run focused tests, stage your changes, then run `prek run --all-files`.
Inspect and stage any hook fixes, and rerun until the checks pass.
Do not disable rules or record debt just to pass a check.
```

The last line matters. An agent under pressure to finish will otherwise lower a
severity or add a suppression; policy edits should be a human's decision.

## 3. Before the edit: context

```bash
smelt context src/shop/application/refunds.py --guide
```

```text
Guide: shop.application.refunds (src/shop/application/refunds.py) [planned]
Layer: application

Required:
  ARC001  From layer application, import only domain; reach other layers through a port

Expected:
  TYP003  Check optional parameters with is not None, not truthiness, before falling
          back

Tests (tests/application/test_refunds.py)

Required:
  TST001  Put test files where tests.layout expects them
```

`--guide` turns every active rule into one instruction for the file, and names
the mirrored test path. Without `--guide`, `context` describes the policy: the
layer and feature, what it may import, third-party allowances, coverage and the
current findings. It accepts feature names, dotted modules and paths, including
files and features that do not exist yet. Add `--format json` for tools.

## 4. After the edit: changed checks

```bash
smelt check --changed --format concise
```

```text
src/shop/application/checkout.py:14:12 TYP003 typing/implicit-optional-default
  optional parameter 'order_cents' uses truthiness for its default -> Use an explicit is not None conditional ...
```

`--changed` reports only findings the working tree introduced since `HEAD`,
including staged and unstaged work. It still analyzes the whole project, so an
API edit that breaks an unchanged caller is reported. Use
`--changed --base origin/main` to review a whole branch, and make sure the base
is available locally.

## 5. At commit time: the staged hook

Install [prek](https://prek.j178.dev/installation/), add the hook to
`.pre-commit-config.yaml` and run `prek install`:

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

The hook checks the Git index rather than the working tree, including partial
staging, deletions and policy edits, and blocks new warnings and errors. The
agent gets the finding, fixes it, stages the fix and retries. Keep
`always_run: true` so edits to shared policy files are checked even when no
Python file changed.

To block on every existing warning, not just new ones, use
`smelt check --fail-on warning` as the entry instead.

## 6. In CI: the full check

```yaml
- uses: astral-sh/setup-uv@v7
- run: prek run --all-files --show-diff-on-failure
- run: uvx --from smelt-cli==0.15.0 smelt check --format github
```

`--format github` turns findings into pull request annotations; `--format sarif`
feeds code scanning. Pin the same version as the hook and update both together.
See [integrations](integrations.md) for code scanning, source installs and
`language: system` hooks.

## Speed

Local checks reuse a background worker and cache analysis by file content, so
the loop stays fast between edits. In one documented benchmark (250 modules, warm
cache), an unchanged staged retry took about 50 ms and a check after a Python
edit about 380 ms, including process startup. The first cache build took a few
seconds. Measure your own project; see the
[agent workflow](agent-workflow.md) for the setup and raw numbers. Set
`SMELT_DAEMON=0` to run without the worker.

## Next

[Level 5: Existing codebases](05-existing-codebases.md) shows how to adopt this
loop in a project that already has hundreds of findings.

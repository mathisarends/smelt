# Agents, hooks and CI

## Python versions

Smelt runs on Python 3.12 to 3.14 and parses your code with the Python it runs on. Code
that uses newer syntax (3.14's `except A, B:` or t-strings, 3.13's type parameter defaults)
needs smelt on that version, e.g. `uvx -p 3.14 --from smelt-cli smelt check`; the syntax error says so when
`requires-python` or `.python-version` targets a newer Python.

The installed command uses a native client and reuses a bounded worker for local
read-only checks. Use `SMELT_DAEMON=0 smelt check` or `python -m smelt check`
to run directly. A checkout or source distribution requires Rust 1.88+ and a
platform linker; release wheels include the compiled client.


## Using Smelt with coding agents

Add this to your `AGENTS.md` or `CLAUDE.md`:

```md
Before implementing, run `smelt context <feature-or-path>` to see what applies.
After editing, run `smelt check --changed --format json` and fix new findings.
Use `smelt explain <code>` when unsure, then run focused tests. Stage the intended
changes and run `smelt check --staged --fail-on warning` and `prek run --all-files`.
If a hook changes files or fails, inspect its output,
fix the cause, and rerun the checks until they pass. Review and stage any hook
changes before committing. Do not relax policy or record debt just to pass.
```

If your project adopts strict coverage, add `--strict-coverage` to the check.
It is a project-wide gate even when reporting only changed findings. Changed
checks default to comparison with `HEAD`, so they include staged and unstaged
work but do not compare existing commits with the target branch; use
`--changed --base origin/main` for that comparison and make sure the base is
available locally. CI should still run the full check. Use tests and a type
checker as appropriate for the change.


## prek and pre-commit

Install [prek](https://prek.j178.dev/installation/) and add the hook to
`.pre-commit-config.yml`:

```yaml
repos:
  - repo: https://github.com/mathisarends/smelt
    rev: v0.1.6
    hooks:
      - id: smelt
```

Run `prek install` to enable the commit hook and `prek run --all-files`
to check tracked files before committing or in CI. The Smelt hook checks the
whole project when Python files or `smelt.yaml` / `smelt.yml` change. Checking
only changed filenames can miss violations in modules that import them. Other
hooks can modify files; inspect their changes, rerun and stage the result.

For versions containing the `smelt-staged` hook, use that ID for commit-time
feedback on the Git index. It checks staged source and policy, including partial
staging and deletions, and fails on new warnings and errors. The `smelt` hook
above runs a full working-tree check. See the [agent workflow](agent-workflow.md)
for cache behavior and limitations. Git refs containing the native client require
Rust 1.88+ and a platform linker to build. The `v0.1.6` example above predates the
native client and `smelt-staged`.

Smelt also supports upstream [pre-commit](https://pre-commit.com/): use its
default `.pre-commit-config.yaml` filename and `pre-commit` commands. The
published `.pre-commit-hooks.yaml` manifest keeps that required filename.


## GitHub Actions

After checkout, run the project's hooks and a full Smelt check. This
example pins Smelt to the same version as the hook above; update both together.
Install prek in the CI environment before running it (or use `uvx prek run`).

```yaml
- uses: astral-sh/setup-uv@v7
- run: prek run --all-files --show-diff-on-failure
- run: uvx --from smelt-cli==0.1.6 smelt check --format github
# optional: code scanning
- run: uvx --from smelt-cli==0.1.6 smelt check --format sarif > smelt.sarif || true
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: smelt.sarif
```

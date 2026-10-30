# Fast commit feedback for coding agents

An agent writes code, stages it and attempts a commit. Smelt returns new findings
with locations, evidence and repair hints. The agent reviews them, changes the code
or records a specific justified exception, stages again and retries the commit.
Smelt never runs the agent or rewrites source in this workflow.

## Enable the review policy and hook

Enable SMT316 explicitly; the hook alone does not enable review rules.
For example:

```yaml
version: 1
project:
  root_packages: [app]
structure:
  contracts:
    internal-helpers:
      match: app
      rules:
        internal-public-member: warning
```

In the existing Smelt pre-commit repository entry, choose `id: smelt-staged`.
This hook runs `smelt check --staged --fail-on warning`, without passing filenames,
and always runs so deletions and configuration-only changes receive a check too.
The existing `smelt` hook retains full-project behavior. A manual invocation of the
staged hook, including `pre-commit run --all-files`, still checks the Git index.
Use `smelt check` for the full working-tree inventory and CI.

`--staged` reads immutable Git blobs: source, config, Python metadata and debt all
come from the captured index. Partial staging works; untracked files and unstaged
repairs cannot change its verdict. The default comparison is HEAD; `--base REF`
uses its merge-base. Before the first commit, all findings are new. Rule selection,
suppression, debt and strict coverage retain their normal semantics: strict
coverage gaps remain visible even when they predate the diff.

Staged JSON/SARIF scope is `staged`; `comparison.working_tree` is false and
`comparison.index_hash` identifies the captured index manifest. Report paths refer
to the original project/repository, including nested config files. Source and
test roots can reach sibling directories inside the repository. External roots,
symbolic links and unresolved index conflicts are rejected explicitly. Submodule
contents are not part of this repository's staged snapshot.

`--staged --fix` is rejected: fix working files, stage the edits and retry.

## Keep a deliberate public API

SMT316 means no external use was observed in the analyzed code. It does not prove
the method can be renamed: plugins, frameworks or downstream packages may call it.

```python
class Plugin:
    def on_event(self, event):  # smelt: ignore[SMT316] -- Public callback invoked by plugin hosts.
        ...
```

Place the comment on the reported definition line. For a multiline signature,
that is the line starting with `def` or `async def`. State the actual API contract;
"intentional" alone gives the next reviewer little evidence. For public module
functions, explicit `__all__` exports can express the interface instead.

By default SMT902 reports missing reasons, and SMT901 reports obsolete exceptions.
With the staged hook's warning threshold, a newly added reasonless exception blocks
the commit. Smelt cannot verify the truth of a reason. Agents should preserve the
project's policy and review the reported evidence rather than disable rules or
record broad debt merely to obtain a passing commit.

## Cache behavior

Add `.smelt_cache/` to `.gitignore`. A SQLite database batches validated JSON facts;
no pickle or executable project objects are deserialized. Removing it is safe. An
unreadable, truncated, locked or unwritable cache falls back to analysis.

Per-file member facts depend on content hashes and module identity. Cross-file
use, inheritance and conservative same-named attribute evidence are resolved
against the current inventory every time analysis changes. Removing a caller can
therefore report an unchanged definition. Ordinary edits still rebuild global
rule decisions; this is not yet a fully incremental engine for every rule/index.
SMT430's attribute evidence is still rebuilt on an analysis cache miss; the
per-file cache currently accelerates function and method evidence.

Complete analyses and baseline fingerprints are also reused. An exact staged index
can bypass source discovery on a repeat check. Normal working-tree checks hash
contents and rediscover inventory; they do not trust modification time alone.
Policy, Python metadata and analyzer/dependency versions invalidate relevant data.
Editable analyzer source changes invalidate its cache too. SQLite transactions
allow simultaneous readers/writers without sharing mutable graph caches. A busy
writer causes a quick fallback instead of making a commit wait for another check.

```bash
smelt check --staged --fail-on warning
smelt check --changed --format json
smelt check --staged --no-cache --format json
```

The cold path includes initial analysis and cache writes. Compare subsequent small
edits and retries separately; an unchanged retry alone is not an edit benchmark.

## Reproduce the timings

```bash
uv run python scripts/benchmark_checks.py --modules 250 --methods 12 --repeats 5
# Optional comparison with a previous checkout/import root:
uv run python scripts/benchmark_checks.py --baseline ../smelt-before
```

The runner creates a disposable Git repository, commits existing public-helper
findings, stages a new unused function and measures complete CLI subprocesses.
It reports an uncached staged run, the first cached run, repeated warm runs and
the first run after another one-file edit. Python startup, Git, snapshot reads,
analysis and JSON output are included. Reports must match cached/uncached execution;
the optional baseline compares the old working-tree diff decisions on the same
source and policy. This synthetic member-policy benchmark does not represent
every architecture, test-quality policy, machine or repository size.

Local sample on 2026-10-30: Windows, CPython 3.14, baseline `d4a77c9`, 250 modules
with 12 existing public helper methods each. Baseline/warm medians use 15 CLI
processes; cold, uncached and edit measurements are single runs. All comparisons
reported the same one introduced finding. These are observations, not a latency
guarantee; temporary snapshot I/O and Python/Git startup remain substantial.

| Scenario | Seconds |
| --- | ---: |
| Previous `--changed`, median | 6.81 |
| Staged, cache disabled | 7.60 |
| Staged, first cache build | 5.33 |
| Staged, unchanged retry, median | 2.79 |
| Staged, unchanged retry, maximum | 4.27 |
| Staged, first check after another file edit | 2.51 |

The warm samples ranged from 1.25 to 4.27 seconds; baseline samples ranged from
5.82 to 18.87 seconds. See the [raw samples](benchmarks/staged-checks-2026-10-30.json).
This substantial run-to-run variation is why a best-case number is not a latency
promise. A baseline checkout must accept the same problem-name rule configuration.

The sub-500-ms target is not achieved by this first implementation. It retains
full rule decisions after edits, caches function/method evidence and avoids repeat
baseline analysis. A further iteration should measure snapshot materialization
and interpreter/import startup separately before choosing a daemon or native code.

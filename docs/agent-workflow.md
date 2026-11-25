# Fast commit feedback for coding agents

An agent writes code, stages it and attempts a commit. Smelt returns new findings
with locations, evidence and repair hints. The agent reviews them, changes the code
or records a specific justified exception, stages again and retries the commit.
Smelt never runs the agent or rewrites source in this workflow.

## Enable the review policy and hook

Enable VIS001 explicitly; the hook alone does not enable review rules.
For example:

```yaml
version: 2
project:
  root_packages: [app]
style:
  overrides:
    internal-helpers:
      match: app
      rules:
        visibility/internal-public-member: warning
```

In the existing Smelt prek repository entry, choose `id: smelt-staged`.
This hook runs `smelt check --staged --fail-on warning`, without passing filenames,
and always runs so deletions and configuration-only changes receive a check too.
The existing `smelt` hook retains full-project behavior. A manual invocation of the
staged hook, including `prek run --all-files`, still checks the Git index.
Use `smelt check` for the full working-tree inventory and CI.

Installing this native build from a Git checkout, including the hook's Python
environment, requires Rust 1.98+ and a platform linker during installation. Release
wheels contain the compiled client. The same hook entry works with pre-commit and
prek; the invoking repository chooses its runner.

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

VIS001 means no external use was observed in the analyzed code. It does not prove
the method can be renamed: plugins, frameworks or downstream packages may call it.

```python
class Plugin:
    def on_event(self, event):  # smelt: ignore[VIS001] -- Public callback invoked by plugin hosts.
        ...
```

Place the comment on the reported definition line. For a multiline signature,
that is the line starting with `def` or `async def`. State the actual API contract;
"intentional" alone gives the next reviewer little evidence. For public module
functions, explicit `__all__` exports can express the interface instead.

By default MET002 reports missing reasons, and MET001 reports obsolete exceptions.
With the staged hook's warning threshold, a newly added reasonless exception blocks
the commit. Smelt cannot verify the truth of a reason. Agents should preserve the
project's policy and review the reported evidence rather than disable rules or
record broad debt merely to obtain a passing commit.

## Performance

Checks keep no cache and leave no background process behind. Every run
analyzes the working tree or the staged index afresh in the compiled native
core, so a result never depends on an earlier run, and there is no
`.smelt_cache/` directory to ignore or delete.
`--no-cache` is still accepted and has no effect.

Staged checks read source and metadata directly from the captured Git blobs,
including the historical comparison. They do not create temporary source trees.
File discovery, config discovery and Python targets all use the same captured
view, including files removed or repaired only in the working tree.

The installed native client provides a read-only Git reader. It captures HEAD
and the complete current index together, then reads objects directly, without
Git child processes. It supports SHA-1/SHA-256, packed objects, linked
worktrees, split indexes and `GIT_INDEX_FILE`, including temporary indexes used
for partial commits. Unsupported inputs, other Git environment overrides or a
failed reader fall back to Git. `--base` resolution still uses Git.
`SMELT_NATIVE_GIT=0` disables the native reader for diagnosis.

The PR hook-latency job compares the complete pre-commit chain of a revision
with its base; see [CONTRIBUTING.md](../CONTRIBUTING.md) to reproduce it.
Earlier measurements of the cached Python engine remain in
[docs/benchmarks](benchmarks/).

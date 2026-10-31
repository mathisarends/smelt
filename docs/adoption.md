# Adopting Smelt in an existing project

A first check often reports dozens of findings that come down to a few decisions. Each
import finding carries an `edge` in JSON (`"billing.application -> voice.domain"`; for
cycles, the cycle), and the text output ends with the edges behind several findings.
Settle each edge once: fix the code, or record an intended dependency as policy, e.g. a
`cross_feature.allow` entry.
JSON also lists `groups`, sorted by size: a `facade` group collects the indirect
findings one module relays (its count is what narrowing that facade's exports would
address), an `edge` group counts `direct` and `transitive` findings of one dependency, a
`cycle` group carries the witness import path, and a `test_move` group the SMT401 files
moving between the same directories. Groups cite findings by their `id`; every finding
stays in `violations`, so a group is one decision, not one more defect.

Accept what remains with a baseline:

`smelt debt` records today's violations in `.smelt/debt.json` and sets `debt:` in
`smelt.yaml`. `smelt check` then fails only on new violations, and SMT903 reports entries
that were fixed and can leave the file (`smelt debt --prune`).
Existing debt files remain readable. Run `smelt debt --prune` once before editing imports
covered by an older baseline to upgrade their fingerprints; pruning accepts no new debt.
New import fingerprints survive comments, aliases and formatting changes.
Cycle debt records each cyclic architecture edge, so a new edge cannot hide behind
an existing shortest-cycle witness. Indirect import findings identify the final
forbidden target, rather than only the facade. Older cycle and indirect-import debt
may therefore reappear for review: the old fingerprints cannot establish the full
dependency they accepted. `smelt debt --prune` removes stale entries without accepting
these findings; review them before explicitly recording new debt.

Override severities by rule name or code, e.g. `rules: {unclassified-module: off}`.

Silence a single finding inline, always with a reason:

```python
from gateway.infra.sql import Repo  # smelt: ignore[SMT101] -- migration tracked in #123
```

Every rule has a page under [docs/rules](rules/). `smelt init` writes a local
editor schema with completion, validation and bad/good hover examples derived
from that same catalog. See [editor setup](configuration.md#editor-completion-hover-and-validation).

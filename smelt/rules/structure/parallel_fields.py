import ast

from smelt.analysis.context import Index
from smelt.diagnostics.violation import Category, Severity
from smelt.rules.base import Rule, RuleDoc

type _Method = ast.FunctionDef | ast.AsyncFunctionDef


class ParallelRecordFields(Rule):
    code = "MOD012"
    name = "modeling/parallel-record-fields"
    category = Category.MODELING
    default_severity = Severity.HINT
    requires = frozenset({Index.SYNTAX})
    doc = RuleDoc(
        summary="Stored sequence fields of one instance are paired by position.",
        rationale=(
            "Parallel stored sequences leave their record relationship implicit: "
            "every constructor and transformation must preserve matching positions. "
            "zip(strict=True) checks lengths but cannot check the right pairing. "
            "This opt-in design review groups zip calls over at least two distinct "
            "sequence fields on the same instance, once per class and field group, "
            "with declaration and zip locations. It checks directly declared class "
            "annotations of list, tuple, Sequence and MutableSequence, including "
            "typing spellings, quoted annotations and module/import aliases. "
            "Preceding unconditional local aliases assigned once from a receiver "
            "field are supported. "
            "Only undecorated instance methods are inspected. Rebound names, starred "
            "arguments, inherited/untyped fields, ClassVar, nested functions and "
            "unknown types are skipped. Local sequences and different receivers "
            "remain valid. Tests are ignored. The rule cannot establish that the "
            "fields form a domain record: intentional column-oriented or numerical "
            "storage is valid and can use a reasoned suppression at the reported zip site."
        ),
        bad=(
            "class Layout:\n"
            "    models: tuple[type, ...]\n"
            "    fields: tuple[tuple[str, ...], ...]\n\n"
            "    def split(self):\n"
            "        for model, names in zip(self.models, self.fields, strict=True):\n"
            "            yield model, names"
        ),
        good=(
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class Binding:\n"
            "    model: type\n"
            "    names: tuple[str, ...]\n\n"
            "class Layout:\n"
            "    bindings: tuple[Binding, ...]\n\n"
            "    def split(self):\n"
            "        for binding in self.bindings:\n"
            "            yield binding.model, binding.names\n\n"
            "class Columns:\n"
            "    x: list[float]\n"
            "    y: list[float]\n\n"
            "    def points(self):\n"
            "        # smelt: ignore[MOD012] -- deliberate numerical column storage\n"
            "        return zip(self.x, self.y, strict=True)"
        ),
        fix=(
            "Review whether these fields model one logical record. If so, store "
            "one sequence of named records and update construction and consumers "
            "together. Preserve length validation and iteration order. Deliberate "
            "column storage may retain a reasoned suppression. No automatic rewrite "
            "or correctness claim is made. Enable rules: {modeling/parallel-record-fields: hint}, "
            "or select MOD012."
        ),
        canonical=(
            "Store one sequence of named records instead of parallel sequence fields"
        ),
        config=("rules", "style.overrides"),
    )

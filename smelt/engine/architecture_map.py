from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from smelt.diagnostics.violation import Severity
from smelt.model import ModuleKind

if TYPE_CHECKING:
    from smelt.analysis.context import AnalysisContext
    from smelt.diagnostics.report import Report
    from smelt.diagnostics.violation import Violation

SCHEMA_VERSION = 1


def _counts(violations: list[Violation]) -> dict[str, int]:
    return {
        "errors": sum(v.severity is Severity.ERROR for v in violations),
        "warnings": sum(v.severity is Severity.WARNING for v in violations),
        "hints": sum(v.severity is Severity.HINT for v in violations),
    }


def _edges(counter: Counter[tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {"source": source, "target": target, "count": count}
        for (source, target), count in sorted(counter.items())
    ]


def build_architecture_map(ctx: AnalysisContext, report: Report) -> dict[str, Any]:
    model = ctx.model
    files = ctx.files
    by_module: dict[str, list[Violation]] = defaultdict(list)
    for violation in report.violations:
        source = files.source_for_path(violation.path) if violation.path else None
        module = violation.source_module or (source.module if source else None)
        if module is not None:
            by_module[module].append(violation)

    feature_edges: Counter[tuple[str, str]] = Counter()
    layer_edges: Counter[tuple[str, str]] = Counter()
    for detail in ctx.imports.all_imports():
        if detail.external:
            continue
        importer, imported = model.info(detail.importer), model.info(detail.imported)
        if importer is None or imported is None:
            continue
        if (
            importer.feature
            and imported.feature
            and importer.feature != imported.feature
        ):
            feature_edges[(importer.feature, imported.feature)] += 1
        if importer.layer and imported.layer and importer.layer != imported.layer:
            layer_edges[(importer.layer, imported.layer)] += 1

    modules = sorted(model.modules.values(), key=lambda info: info.name)
    features = []
    for name, feature in sorted(model.features.items()):
        members = [info for info in modules if info.feature == name]
        layers: dict[str, list[str]] = {layer: [] for layer in model.layers}
        outside: list[str] = []
        for info in members:
            layers.get(info.layer or "", outside).append(info.name)
        features.append(
            {
                "name": name,
                "package": feature.package,
                "modules": len(members),
                "layers": layers,
                "outside_layers": outside,
                "violations": _counts(
                    [v for info in members for v in by_module.get(info.name, [])]
                ),
            }
        )

    def kind(value: ModuleKind) -> list[str]:
        return [info.name for info in modules if info.kind is value]

    return {
        "schema_version": SCHEMA_VERSION,
        "root_packages": list(ctx.config.project.root_packages),
        "modules": len(modules),
        "features": features,
        "layers": [
            {
                "name": name,
                "path": layer.path,
                "may_depend_on": list(layer.may_depend_on),
                "modules": sum(info.layer == name for info in modules),
                "violations": _counts(
                    [
                        v
                        for info in modules
                        if info.layer == name
                        for v in by_module.get(info.name, [])
                    ]
                ),
            }
            for name, layer in model.layers.items()
        ],
        "shared": kind(ModuleKind.SHARED),
        "composition_root": kind(ModuleKind.COMPOSITION_ROOT),
        "unclassified": kind(ModuleKind.UNCLASSIFIED),
        "roles": [
            {
                "role": match.role,
                "class": match.cls.qualname,
                "path": match.cls.path,
                "line": match.cls.line,
            }
            for match in ctx.roles.matches()
        ],
        "edges": {"features": _edges(feature_edges), "layers": _edges(layer_edges)},
        "violations": {
            "total": _counts(report.violations),
            "by_module": {
                module: _counts(found) for module, found in sorted(by_module.items())
            },
        },
    }

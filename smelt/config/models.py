import builtins
import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, NamedTuple, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

type Policy = Literal["allow", "deny"]
type RuleSeverity = Literal["error", "warning", "hint", "on", "off"]
type ModuleImportExemption = Literal["facades", "composition_root"]
type PublicAttributeBoundary = Literal["class", "module"]
type PrivateAttributeBoundary = Literal["module", "package"]
type CallArgumentKind = Literal["await", "comprehension", "aggregate"]
type ReexportStyle = Literal["all", "alias", "allow"]

_DOTTED_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
ALL_RULES = "ALL"
_RULE_CODE = re.compile(r"^[A-Z]+[0-9]+$")
_RULE_NAME = re.compile(
    r"^[a-z][a-z0-9]*(-[a-z0-9]+)*(/([a-z][a-z0-9]*(-[a-z0-9]+)*)?)?$"
)
_LAYER_PAIR = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*->\s*([A-Za-z_][\w.]*)\s*$")
_FEATURE_LAYER = re.compile(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)$")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RuleOverride(_Model):
    """A rule entry: its severity, its limit and the reason for an exception."""

    severity: RuleSeverity | None = Field(
        default=None,
        description="Severity of the rule here. Omitted keeps the inherited severity, so a package or member entry can set only max.",
    )
    max: int | None = Field(
        default=None,
        ge=0,
        description="The rule's limit, for rules that count something, e.g. complexity/excess-positional-parameters. Package and member entries refine the root limit for their files.",
    )
    reason: str | None = Field(default=None, min_length=1)

    @field_validator("reason")
    @classmethod
    def _reason_is_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("reason must not be blank")
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def _sets_something(self) -> Self:
        if self.max is None and self.severity is None:
            raise ValueError("a rule entry sets severity, max or both")
        if self.max is None and self.reason is None:
            raise ValueError(
                "reason is required when the entry sets only a severity; write the severity directly otherwise"
            )
        return self


type RuleSetting = RuleSeverity | RuleOverride
# The guard rule whose root setting enables strict policy coverage, by code and name.
UNCOVERED_SOURCE = ("ARC013", "architecture/uncovered-source")
# Rules whose entry takes max, by the compiled policy option that carries the limit.
LIMIT_RULES = {
    "max_module_imports": ("IMP002", "imports/excess-module-imports"),
    "max_positional_parameters": (
        "CPX003",
        "complexity/excess-positional-parameters",
    ),
    "max_comprehension_loops": ("CPX006", "complexity/excess-comprehension-loops"),
    "max_constructor_decisions": (
        "CPX007",
        "complexity/excess-constructor-decisions",
    ),
    "max_private_definitions": ("VIS007", "visibility/excess-private-definitions"),
    "max_patches_per_test": ("TST005", "tests/excess-patches"),
    "max_uncovered_facade_callers": ("MET005", "meta/uncovered-package-facade"),
}
type _PolicyReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1)
]


def _check_dotted(values: list[str]) -> list[str]:
    for value in values:
        if not _DOTTED_NAME.match(value):
            msg = f'"{value}" is not a dotted module name'
            raise ValueError(msg)
    return values


class SourceExclusion(_Model):
    path: str = Field(
        description="Project-relative glob of source files deliberately excluded from analysis."
    )
    reason: str = Field(
        description="Why this code is outside this architecture policy."
    )

    @field_validator("path", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value.strip()


class ProjectConfig(_Model):
    """Which packages and tests smelt reads."""

    root_packages: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Top-level packages to check; discovered from source_roots when omitted.",
        ),
    ]
    source_roots: list[str] = Field(
        default_factory=lambda: ["."],
        description="Directories that contain the root packages, e.g. [src].",
    )
    test_roots: list[str] = Field(
        default_factory=lambda: ["tests"],
        description="Directories with the tests; with layout: mirror they must exist.",
    )
    exclude: list[str | SourceExclusion] = Field(
        default_factory=list,
        description="Path globs or {path, reason} entries smelt skips entirely. architecture/uncovered-source requires a reason for every exclusion.",
    )
    workspace: Literal["uv"] | None = Field(
        default=None,
        description="uv: read members from tool.uv.workspace in pyproject.toml. source_roots and test_roots then apply inside every member, root packages are discovered per member, and a member's own smelt.yml refines this policy for its files.",
    )
    python_version: str | None = Field(
        default=None,
        pattern=r"^3\.[0-9]+$",
        description="Explicit minimum supported Python version for version-aware conventions, e.g. 3.14. Overrides per-project requires-python metadata; never inferred from the running interpreter or .python-version.",
    )

    @property
    def exclude_patterns(self) -> list[str]:
        return [
            entry if isinstance(entry, str) else entry.path for entry in self.exclude
        ]

    @field_validator("source_roots", "test_roots")
    @classmethod
    def _normalize_roots(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(posixpath.normpath(v.replace("\\", "/")) for v in values)
        )

    @field_validator("root_packages")
    @classmethod
    def _root_packages_are_top_level(cls, values: list[str]) -> list[str]:
        for value in _check_dotted(values):
            if "." in value:
                msg = f'"{value}" must be a top-level package name'
                raise ValueError(msg)
        return values


class FeaturesConfig(_Model):
    root: str | None = Field(
        default=None,
        description="Package whose direct child packages are the features.",
    )
    pattern: str | None = Field(
        default=None,
        description='Dotted pattern ending in {feature}, e.g. "app.{feature}".',
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.root is None) == (self.pattern is None):
            msg = "set exactly one of `root` or `pattern`"
            raise ValueError(msg)
        if self.root is not None:
            _check_dotted([self.root])
        if self.pattern is not None:
            segments = self.pattern.split(".")
            if segments.count("{feature}") != 1 or segments[-1] != "{feature}":
                msg = (
                    f'pattern "{self.pattern}" must end with a "{{feature}}" segment, '
                    'e.g. "gateway.{feature}"'
                )
                raise ValueError(msg)
            if len(segments) < 2:  # noqa: PLR2004
                msg = "pattern needs a package before {feature}"
                raise ValueError(msg)
            _check_dotted(segments[:-1])
        return self


class ThirdPartyPolicy(_Model):
    default: Policy = "allow"
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _expand_short_form(cls, data: object) -> object:
        if data in ("allow", "deny"):
            return {"default": data}
        return data

    @model_validator(mode="after")
    def _entries_match_default(self) -> Self:
        if self.default == "allow" and self.allow:
            msg = "`allow` is only valid with `default: deny`"
            raise ValueError(msg)
        if self.default == "deny" and self.deny:
            msg = "`deny` is only valid with `default: allow`"
            raise ValueError(msg)
        _check_dotted([*self.allow, *self.deny])
        return self


class LayerConfig(_Model):
    path: str | None = Field(
        default=None,
        description="Dotted layer path relative to a feature or root package.",
    )
    modules: list[str] = Field(
        default_factory=list,
        description="Qualified module names or package prefixes outside the feature grid assigned to this layer.",
    )
    may_depend_on: list[str] = Field(
        default_factory=list, description="Layers this layer may import."
    )
    third_party: ThirdPartyPolicy = Field(
        default_factory=ThirdPartyPolicy,
        description="Which third-party packages the layer may import (ARC003).",
    )

    @field_validator("path")
    @classmethod
    def _path_is_dotted(cls, value: str | None) -> str | None:
        if value is not None:
            _check_dotted([value])
        return value

    @field_validator("modules")
    @classmethod
    def _modules_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)

    @model_validator(mode="after")
    def _has_placement(self) -> Self:
        if self.path is None and not self.modules:
            raise ValueError("a layer requires path or modules")
        return self


class FeatureLayerEdge(NamedTuple):
    """The features and layers on both sides of a cross-feature import."""

    source_feature: str
    source_layer: str
    target_feature: str
    target_layer: str


class CrossFeatureAllowance(_Model):
    source: str = Field(alias="from")
    target: str = Field(alias="to")

    @model_validator(mode="after")
    def _feature_layers(self) -> Self:
        for value in (self.source, self.target):
            if not _FEATURE_LAYER.fullmatch(value):
                msg = f'"{value}" must look like "feature.layer"'
                raise ValueError(msg)
        return self

    def components(self) -> FeatureLayerEdge:
        source_feature, source_layer = self.source.split(".")
        target_feature, target_layer = self.target.split(".")
        return FeatureLayerEdge(
            source_feature, source_layer, target_feature, target_layer
        )


class CrossFeatureConfig(_Model):
    """Imports between features."""

    default: Policy = Field(
        default="deny", description="Whether features may import each other at all."
    )
    allow: list[str | CrossFeatureAllowance] = Field(
        default_factory=list,
        description='Allowed imports: "layer -> layer" for all features, or {from: feature.layer, to: feature.layer}.',
    )

    @field_validator("allow")
    @classmethod
    def _pairs_are_well_formed(
        cls, values: list[str | CrossFeatureAllowance]
    ) -> list[str | CrossFeatureAllowance]:
        for value in values:
            if isinstance(value, str) and not _LAYER_PAIR.match(value):
                msg = f'"{value}" must look like "layer -> layer"'
                raise ValueError(msg)
        return values

    def pairs(self) -> frozenset[tuple[str, str]]:
        result: set[tuple[str, str]] = set()
        for value in self.allow:
            if not isinstance(value, str):
                continue
            match = _LAYER_PAIR.match(value)
            if match:
                result.add((match.group(1), match.group(2)))
        return frozenset(result)

    def allows(
        self,
        source_feature: str,
        source_layer: str,
        target_feature: str,
        *,
        target_layer: str,
    ) -> bool:
        if (source_layer, target_layer) in self.pairs():
            return True
        edge = FeatureLayerEdge(
            source_feature, source_layer, target_feature, target_layer
        )
        return any(
            isinstance(value, CrossFeatureAllowance) and value.components() == edge
            for value in self.allow
        )

    def labels(self) -> list[str]:
        return [
            value if isinstance(value, str) else f"{value.source} -> {value.target}"
            for value in self.allow
        ]

    def entries(self) -> list[dict[str, str | None]]:
        """Each allowance as fields; a ``None`` feature stands for every feature."""
        found: list[dict[str, str | None]] = []
        for value in self.allow:
            if isinstance(value, CrossFeatureAllowance):
                found.append(value.components()._asdict())
            elif match := _LAYER_PAIR.match(value):
                found.append(
                    {
                        "source_feature": None,
                        "source_layer": match.group(1),
                        "target_feature": None,
                        "target_layer": match.group(2),
                    }
                )
        return found


type CycleScope = Literal["features", "layers", "siblings", "modules"]


def _all_cycle_scopes() -> list[CycleScope]:
    return ["features", "layers", "siblings"]


class ImportsConfig(_Model):
    """Which imports the dependency rules look at."""

    type_checking: Literal["include", "ignore"] = Field(
        default="include",
        description="Whether imports under `if TYPE_CHECKING:` count.",
    )
    transitive: bool = Field(
        default=False,
        description="Also check indirect layer, feature, shared, composition-root and third-party dependencies through all reachable project modules. Reports a shortest import chain; external packages are leaves.",
    )
    cycles: list[CycleScope] = Field(
        default_factory=_all_cycle_scopes, description="Where ARC004 looks for cycles."
    )
    type_checking_cycles: Literal["include", "ignore"] = Field(
        default="include",
        description="include follows the global type_checking setting; ignore excludes TYPE_CHECKING edges from cycle graphs only, retaining their configured treatment in other boundary checks. The modules scope checks concrete modules; existing scopes aggregate architecture nodes.",
    )


def _member_pattern(value: str) -> str:
    pattern = value.strip().replace("\\", "/")
    if (
        not pattern
        or pattern.startswith("/")
        or re.match(r"^[A-Za-z]:", pattern)
        or ".." in pattern.split("/")
    ):
        raise ValueError(
            "member globs must be nonempty workspace-relative paths without '..'"
        )
    return posixpath.normpath(pattern)


class MemberBoundaryConfig(_Model):
    """Allowed target members for each matching source member."""

    may_depend_on: list[str] = Field(
        default_factory=list,
        description="Workspace-relative member globs this member may import; [] forbids imports of other members. Overlapping source entries combine their allowances.",
    )

    @field_validator("may_depend_on")
    @classmethod
    def _member_globs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_member_pattern(value) for value in values))


class _PackageTarget(_Model):
    match: str = Field(
        description="Dotted package pattern; a whole-segment * matches one package segment."
    )

    @field_validator("match")
    @classmethod
    def _package_pattern(cls, value: str) -> str:
        if any(
            segment != "*" and not _DOTTED_NAME.fullmatch(segment)
            for segment in value.split(".")
        ):
            raise ValueError(
                "must be a dotted package pattern with whole-segment * wildcards"
            )
        return value


class PackageBoundary(_PackageTarget):
    """Public package entry points, required modules and facade thresholds."""

    required: list[str] = Field(
        default_factory=list,
        description="Relative dotted modules or packages that must exist in every matching package (ARC014).",
    )
    public: list[str] | None = Field(
        default=None,
        description="Relative public module patterns (ARC007). '.' exposes the package facade; * matches one segment, ** any depth. Omitted: no interface restriction; []: private package. Each pattern must match analyzed code.",
    )
    min_imports_to_bundle: int | None = Field(
        default=None,
        ge=2,
        description="Suggest a package facade when one outside source module imports this many distinct paths within the package, counting a mixed facade/implementation import too (IMP001, hint). Includes proposed exports and runtime cycle evidence; no automatic rewrite.",
    )
    min_importers_to_export: int | None = Field(
        default=None,
        ge=2,
        description="Review a missing facade export when this many distinct outside modules import a public implementation name (ARC008, hint). Counts runtime source imports by default; no automatic API changes.",
    )
    count_tests: bool = Field(
        default=False,
        description="Include discovered test files in min_importers_to_export counts. Requires min_importers_to_export; TYPE_CHECKING-only imports never count.",
    )
    test_access: Literal["public", "mirrored"] = Field(
        default="public",
        description="How discovered tests may import this package (ARC007, IMP001). public: through its public entry points, like any outside caller. mirrored: a test whose tests.mirror path mirrors a module inside the package counts as inside it; conftest.py and other tests stay outside callers.",
    )

    @field_validator("required")
    @classmethod
    def _required_modules(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_check_dotted(values)))

    @field_validator("public")
    @classmethod
    def _public_modules(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        for value in values:
            if value == ".":
                continue
            if any(
                segment not in ("*", "**") and not _DOTTED_NAME.fullmatch(segment)
                for segment in value.split(".")
            ):
                raise ValueError(
                    "public entries must be relative dotted module patterns or '.'"
                )
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _has_export_threshold(self) -> Self:
        if self.count_tests and self.min_importers_to_export is None:
            raise ValueError("count_tests requires min_importers_to_export")
        return self


@dataclass(frozen=True, slots=True)
class LayerBinding:
    prefix: str
    layer: str
    key: str


class ArchitectureConfig(_Model):
    """Features, layers and the modules around them."""

    features: FeaturesConfig | None = Field(
        default=None, description="Where the features (vertical slices) live."
    )
    shared: list[str] = Field(
        default_factory=list,
        description="Modules every feature may import; they must not import features.",
    )
    composition_root: list[str] = Field(
        default_factory=list,
        description="Modules that wire everything; nothing may import them.",
    )
    wiring: list[str] = Field(
        default_factory=list,
        description="Provider modules (whole-segment * allowed) that may import across layers and features; they keep their feature and layer.",
    )
    layers: dict[str, LayerConfig] = Field(
        default_factory=dict, description="The layers and what each may import."
    )
    cross_feature: CrossFeatureConfig = Field(
        default_factory=CrossFeatureConfig, description="Imports between features."
    )
    import_graph: ImportsConfig = Field(
        default_factory=ImportsConfig,
        description="Which imports count in the architecture graph.",
    )
    members: dict[str, MemberBoundaryConfig] = Field(
        default_factory=dict,
        description="Root-only uv workspace boundaries: source member path glob -> {may_depend_on: [target member globs]}. Unmatched source members are unrestricted; '.' names the root project. ARC009 checks direct imports.",
    )

    packages: dict[str, PackageBoundary] = Field(
        default_factory=dict,
        description="Public interfaces and required modules for matching packages.",
    )
    canonical_exports: dict[str, str] = Field(
        default_factory=dict,
        description="Definition or alias to its canonical public export.",
    )

    @property
    def has_layout(self) -> bool:
        """Whether the file declares local architecture."""
        return bool(
            self.model_fields_set
            - {"packages", "canonical_exports", "import_graph", "members"}
        )

    @property
    def has_module_bindings(self) -> bool:
        return any(layer.modules for layer in self.layers.values())

    def module_bindings(self) -> list[LayerBinding]:
        """The explicit bindings and their keys relative to the architecture."""
        return [
            LayerBinding(prefix, layer, f"layers.{layer}.modules[{index}]")
            for layer, spec in self.layers.items()
            for index, prefix in enumerate(spec.modules)
        ]

    @field_validator("canonical_exports")
    @classmethod
    def _canonical_names(cls, values: dict[str, str]) -> dict[str, str]:
        names = [*values, *values.values()]
        _check_dotted(names)
        if any("." not in name for name in names):
            raise ValueError("canonical exports require qualified module.symbol names")
        return values

    @field_validator("members")
    @classmethod
    def _member_patterns(
        cls, values: dict[str, MemberBoundaryConfig]
    ) -> dict[str, MemberBoundaryConfig]:
        result: dict[str, MemberBoundaryConfig] = {}
        for key, policy in values.items():
            pattern = _member_pattern(key)
            if pattern in result:
                raise ValueError(f'duplicate member glob "{pattern}"')
            result[pattern] = policy
        return result

    @field_validator("shared", "composition_root")
    @classmethod
    def _modules_are_dotted(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)

    @field_validator("wiring")
    @classmethod
    def _wiring_patterns(cls, values: list[str]) -> list[str]:
        for value in values:
            if any(
                segment != "*" and not _DOTTED_NAME.fullmatch(segment)
                for segment in value.split(".")
            ):
                msg = f'"{value}" must be a dotted module pattern with whole-segment * wildcards'
                raise ValueError(msg)
        return values


class ImportStyleConfig(_Model):
    style: Literal["relative", "absolute"] = Field(
        default="relative",
        description="relative: prefer relative first-party imports up to max_relative_level, absolute beyond it; absolute: always use absolute imports.",
    )
    max_relative_level: int = Field(
        default=1,
        ge=1,
        description="Maximum dots in a relative import when style is relative. Cross-root and deeper imports stay absolute.",
    )


type DataclassOption = Literal["frozen", "slots", "kw_only"]


type ModuleOrderSlot = Literal[
    "docstring",
    "future-imports",
    "imports",
    "dunder-assignments",
    "type-vars",
    "public",
    "private",
    "main-guard",
]
type ClassOrderSlot = Literal[
    "class-attributes", "init", "dunders", "public", "private"
]


def _module_order() -> list[ModuleOrderSlot]:
    return [
        "docstring",
        "future-imports",
        "imports",
        "dunder-assignments",
        "type-vars",
        "public",
        "private",
        "main-guard",
    ]


def _class_order() -> list[ClassOrderSlot]:
    return ["class-attributes", "init", "dunders", "public", "private"]


class OrderingConfig(_Model):
    module: list[ModuleOrderSlot] = Field(default_factory=_module_order)
    class_order: list[ClassOrderSlot] = Field(
        default_factory=_class_order, alias="class"
    )
    constants_exempt: bool = Field(
        default=False,
        description="Treat private uppercase module constants like public definitions, preserving their relative order.",
    )
    fix: Literal["off", "safe-only"] = Field(
        default="safe-only",
        description="Permit bounded ordering fixes only when check --fix is explicitly requested. Plain check never writes source.",
    )

    @field_validator("module")
    @classmethod
    def _module_slots(cls, values: list[ModuleOrderSlot]) -> list[ModuleOrderSlot]:
        if len(values) != len(set(values)) or set(values) != set(_module_order()):
            raise ValueError(
                "module order must contain every module category exactly once"
            )
        if values.index("public") > values.index("private"):
            raise ValueError("layout/definition-order requires public before private")
        if (
            values[:3] != ["docstring", "future-imports", "imports"]
            or values[-1] != "main-guard"
        ):
            raise ValueError(
                "module order must start with docstring, future-imports, imports and end with main-guard"
            )
        return values

    @field_validator("class_order")
    @classmethod
    def _class_slots(cls, values: list[ClassOrderSlot]) -> list[ClassOrderSlot]:
        if len(values) != len(set(values)) or set(values) != set(_class_order()):
            raise ValueError(
                "class order must contain every class category exactly once"
            )
        if values.index("public") > values.index("private"):
            raise ValueError("layout/definition-order requires public before private")
        return values


class NavigationConfig(_Model):
    namespace_types: list[str] = Field(
        default_factory=list,
        description="Qualified classes whose instances CPX001 treats as namespaces, together with every attribute reached through them, e.g. an SDK client that groups domain APIs: [sdk.Client].",
    )
    instance_factories: list[str] = Field(
        default_factory=list,
        description="Qualified callables that return an instance of the class passed as their first positional argument, such as a typed state descriptor or a dependency marker: [framework.State]. CPX001 then types class attributes and constructor assignments made with them.",
    )

    @field_validator("namespace_types", "instance_factories")
    @classmethod
    def _qualified_names(cls, values: list[str]) -> list[str]:
        for value in values:
            if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", value):
                msg = f"{value!r} is not a qualified name such as package.module.Name"
                raise ValueError(msg)
        return values


class TypeDispatchConfig(_Model):
    style: Literal["allow", "match"] = "match"
    min_branches: int = Field(
        default=3,
        ge=2,
        description="Minimum number of type-tested if/elif branches over the same simple name before IDM002 suggests match/case.",
    )


class ErrorPolicy(_Model):
    base: str | None = Field(
        default=None,
        description="Fully qualified project exception that production code raises instead of builtin exceptions (MOD008), e.g. app.errors.AppError. Omitted: {} lifts an outer policy.",
    )
    allow: list[str] = Field(
        default_factory=list,
        description="Builtin exception names that may still be raised, in addition to NotImplementedError, StopIteration, StopAsyncIteration, GeneratorExit, KeyboardInterrupt and SystemExit, e.g. [TypeError].",
    )

    @field_validator("base")
    @classmethod
    def _base(cls, value: str | None) -> str | None:
        if value is not None and (
            "." not in value or not _DOTTED_NAME.fullmatch(value)
        ):
            raise ValueError(
                f"errors.base must be a fully qualified class name: {value!r}"
            )
        return value

    @field_validator("allow")
    @classmethod
    def _allow(cls, values: list[str]) -> list[str]:
        for value in values:
            if not isinstance(getattr(builtins, value, None), type) or not issubclass(
                getattr(builtins, value), BaseException
            ):
                raise ValueError(
                    f"errors.allow entries must be builtin exception names: {value!r}"
                )
        return list(dict.fromkeys(values))


class PackagePolicy(_Model):
    """Source conventions set for the whole project or for matching packages.

    Each option is defined once. style sets the project-wide value and the
    closest package contract that sets an option overrides it. Setting an option
    that belongs to a single rule enables that rule.
    """

    imports: ImportStyleConfig | None = Field(
        default=None,
        description="Import style (IMP003). Setting it enables imports/import-style-convention. The closest package contract overrides the project-wide style.",
    )
    reexports: ReexportStyle | None = Field(
        default=None,
        description="Package initializer re-export convention (VIS008). all uses __all__; alias uses same-name import aliases. allow permits either spelling. Selecting the rule alone uses all.",
    )
    module_import_exemptions: list[ModuleImportExemption] | None = Field(
        default=None,
        description="IMP002 exemptions: facades skips all package initializers; composition_root skips declared composition roots; [] checks both. Omitted means [facades, composition_root].",
    )
    module_docstrings: Literal["allow", "forbid", "require"] | None = Field(
        default=None,
        description="Module-docstring convention: require a non-empty initial docstring or forbid any initial docstring (LAY002), or allow either. Includes package initializers; function and class documentation is unaffected.",
    )
    future_annotations: Literal["allow", "forbid", "when-needed"] | None = Field(
        default=None,
        description="allow: preserve; forbid: reject the annotations future import (TYP001); when-needed: reject it for a declared Python 3.14+ minimum, otherwise conservatively suggest review when no postponement need is found (TYP002). No automatic rewriting.",
    )
    private_modules: Literal["allow", "forbid"] | None = Field(
        default=None,
        description="Naming convention (LAY003): forbid module and package names starting with _, except the special __init__.py and __main__.py files. Private functions and methods are unaffected.",
    )
    public_attribute_boundary: PublicAttributeBoundary | None = Field(
        default=None,
        description="Visibility boundary for public data attributes: class uses the owning class; module also allows other production code in the defining module. Setting it, including class, enables VIS004. VIS010 uses the same boundary when selected separately. Omitted means class.",
    )
    private_attribute_boundary: PrivateAttributeBoundary | None = Field(
        default=None,
        description="Ownership boundary for private attribute access in visibility/foreign-private-access (VIS005): module allows same-module cooperation; package allows modules of the same immediate Python package. Private module symbols always use the module boundary. Setting it enables the rule. Omitted means module.",
    )
    ordering: OrderingConfig | None = Field(
        default=None,
        description="Definition order for modules and classes. Dependencies and execution barriers take precedence over category priority. Configures layout/definition-order, which is enabled through rules.",
    )
    type_dispatch: TypeDispatchConfig | None = Field(
        default=None,
        description="IDM002 match/case preference for same-subject isinstance chains. No automatic rewrite.",
    )
    navigation: NavigationConfig | None = Field(
        default=None,
        description="Namespace evidence for complexity/excess-attribute-depth (CPX001) about types Smelt cannot analyze, such as third-party SDK clients and typed descriptors. {} lifts an outer policy.",
    )
    call_argument_kinds: list[CallArgumentKind] | None = Field(
        default=None,
        min_length=1,
        description="Argument kinds complexity/inline-call-argument (CPX002) reports: await expressions, list/set/dict comprehensions and builtin reductions of generators (aggregate). Setting it enables the rule. Omitted means all three.",
    )
    str_enum_values: Literal["auto", "explicit"] | None = Field(
        default=None,
        description="StrEnum value convention (MOD004): explicit reports auto() members; auto reports explicit strings that auto() derives, except in classes with custom values. Setting it enables modeling/str-enum-value-convention. Omitted means explicit.",
    )
    dataclass_options: list[DataclassOption] | None = Field(
        default=None,
        description="Dataclass options every production @dataclass must set to True (MOD006): frozen, slots or kw_only. [] lifts an outer requirement. Missing slots is not reported for classes with a base from outside the project or a functools.cached_property. No automatic rewrite.",
    )
    bases: dict[str, str] | None = Field(
        default=None,
        description="Map a base class from outside the project to the project base that classes must inherit instead (MOD007), e.g. {pydantic.BaseModel: app.core.Options}. Keys and values are fully qualified names. Classes in the module that defines the project base are exempt; {} lifts an outer mapping. No automatic rewrite.",
    )
    third_party_reexports: list[str] | None = Field(
        default=None,
        description="Top-level installed import packages, e.g. [fastapi, pydantic], whose initializer re-exports imports/prefer-package-import (IMP004) also suggests. Smelt reads their sources from the interpreter it runs in without importing them; packages it cannot find are skipped. [] lifts an outer list.",
    )
    errors: ErrorPolicy | None = Field(
        default=None,
        description="Project exception policy (MOD008): production code raises subclasses of base instead of builtin exceptions, except names in allow. Raises inside protocol dunder methods and pydantic validators are exempt. {} lifts an outer policy; no automatic rewrite.",
    )

    @field_validator("dataclass_options")
    @classmethod
    def _dataclass_options(
        cls, values: list[DataclassOption] | None
    ) -> list[DataclassOption] | None:
        return None if values is None else list(dict.fromkeys(values))

    @field_validator("module_import_exemptions", "call_argument_kinds")
    @classmethod
    def _unique(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else list(dict.fromkeys(values))

    @field_validator("third_party_reexports")
    @classmethod
    def _third_party_reexports(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        for name in values:
            if "." in name or not _DOTTED_NAME.fullmatch(name):
                raise ValueError(
                    f"third_party_reexports entries must be top-level import names: {name!r}"
                )
        return list(dict.fromkeys(values))

    @field_validator("bases")
    @classmethod
    def _bases(cls, values: dict[str, str] | None) -> dict[str, str] | None:
        if values is None:
            return None
        for outside, project in values.items():
            for name in (outside, project):
                if "." not in name or not all(
                    _DOTTED_NAME.fullmatch(segment) for segment in name.split(".")
                ):
                    raise ValueError(
                        f"bases entries must be fully qualified class names: {name!r}"
                    )
            if outside == project:
                raise ValueError(f"{outside} cannot replace itself")
        return values

    def configured(self) -> bool:
        """Whether any policy option is set at this level."""
        return any(
            getattr(self, name) is not None for name in PackagePolicy.model_fields
        )


class RuleLimits(_Model):
    """Limits from rule entries, compiled into the policy of their scope."""

    max_module_imports: int | None = None
    max_positional_parameters: int | None = None
    max_comprehension_loops: int | None = None
    max_constructor_decisions: int | None = None
    max_private_definitions: int | None = None


# Every option a compiled policy resolves per package: style options, then limits.
POLICY_OPTIONS = (*PackagePolicy.model_fields, *RuleLimits.model_fields)


def _rule_limits(rules: Mapping[str, RuleSetting]) -> dict[str, int]:
    """The max of each limit rule that ``rules`` sets, by its policy option."""
    limits = {}
    for option, keys in LIMIT_RULES.items():
        for key in keys:
            setting = rules.get(key)
            if isinstance(setting, RuleOverride) and setting.max is not None:
                limits[option] = setting.max
    return limits


@dataclass(frozen=True, slots=True)
class PolicyDefaults:
    """Values used when neither structure nor a package contract sets an option."""

    max_module_imports: int = 20
    module_import_exemptions: tuple[ModuleImportExemption, ...] = (
        "facades",
        "composition_root",
    )
    public_attribute_boundary: PublicAttributeBoundary = "class"
    private_attribute_boundary: PrivateAttributeBoundary = "module"
    max_positional_parameters: int = 5
    max_comprehension_loops: int = 1
    max_constructor_decisions: int = 2
    max_private_definitions: int = 12
    max_uncovered_facade_callers: int = 2
    call_argument_kinds: tuple[CallArgumentKind, ...] = (
        "await",
        "comprehension",
        "aggregate",
    )
    str_enum_values: Literal["auto", "explicit"] = "explicit"
    reexports: ReexportStyle = "all"


POLICY_DEFAULTS = PolicyDefaults()


class _ReasonedStyleValue(_Model):
    value: Any
    reason: _PolicyReason


class _ReasonedStyle(PackagePolicy):
    reasons: dict[str, _PolicyReason] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _inline_reasons(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        data.pop("reasons", None)
        for name, reason in self.reasons.items():
            if name in data:
                data[name] = {"value": data[name], "reason": reason}
        return data

    @model_validator(mode="before")
    @classmethod
    def _read_inline_reasons(cls, raw: object) -> object:
        if not isinstance(raw, dict):
            return raw
        values = dict(raw)
        legacy = values.get("reasons", {})
        if not isinstance(legacy, dict):
            return raw
        reasons = dict(legacy)
        for name in PackagePolicy.model_fields.keys() & values.keys():
            value = values[name]
            if not isinstance(value, dict) or not ({"value", "reason"} & value.keys()):
                continue
            if name in reasons:
                raise ValueError(f"{name} already has a reason in reasons.{name}")
            try:
                setting = _ReasonedStyleValue.model_validate(value)
            except ValidationError as exc:
                raise ValueError(f"{name}: {exc.errors()[0]['msg']}") from exc
            values[name], reasons[name] = setting.value, setting.reason
        if reasons:
            values["reasons"] = reasons
        return values

    @model_validator(mode="after")
    def _reasons_explain_options(self) -> Self:
        for name in self.reasons:
            if name not in POLICY_OPTIONS or getattr(self, name, None) is None:
                raise ValueError(f"reasons.{name} must explain a style option set here")
        return self


class StyleOverride(_ReasonedStyle, _PackageTarget):
    """Conventions and rule severity choices for matching packages."""

    rules: dict[str, RuleSetting] = Field(
        default_factory=dict,
        description="Rule severities for production findings in this package. Enable individual rules by name or code. Families and ALL can disable or cap active source rules. Individual entries override broad entries, and the closest package override wins.",
    )


class PackageContract(PackageBoundary, StyleOverride, RuleLimits):
    """Compiled package policy for rules that consume style and API together."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)
    _style_location: str | None = PrivateAttr(default=None)
    _style_override: bool = PrivateAttr(default=False)

    @property
    def style_location(self) -> str | None:
        """Input key of the style portion of this compiled policy."""
        return self._style_location

    @property
    def style_override(self) -> bool:
        """Whether the style comes from an explicit package override."""
        return self._style_override

    def with_style_location(self, location: str, *, override: bool = False) -> Self:
        policy = self.model_copy()
        policy._style_location = location
        policy._style_override = override
        return policy


class FileNamingConfig(_Model):
    """Canonical spellings for project configuration filenames."""

    yaml_extension: Literal["yaml", "yml"] | None = Field(
        default=None,
        description="Canonical YAML extension, without a dot (LAY007). Omitted means either spelling is allowed. Applies below the project root, including hidden configuration directories, subject to project.exclude.",
    )
    compose_basename: Literal["compose", "docker-compose"] | None = Field(
        default=None,
        description="Canonical Compose basename (LAY007), including dotted variants such as compose.override.yaml. The extension is controlled only by yaml_extension. Omitted means either basename is allowed.",
    )


class _ProjectStyle(_ReasonedStyle):
    forbidden_names: list[str] = Field(
        default_factory=list, description="Package and module names to avoid (LAY001)."
    )
    file_naming: FileNamingConfig = Field(default_factory=FileNamingConfig)


class StyleConfig(_ProjectStyle):
    """Project conventions and source-rule settings for matching packages."""

    overrides: dict[str, StyleOverride] = Field(default_factory=dict)


class StructureConfig(_ProjectStyle, RuleLimits):
    """Effective style, plus architecture and style contracts compiled for analysis."""

    contracts: dict[str, PackageContract] = Field(default_factory=dict)
    canonical_exports: dict[str, str] = Field(default_factory=dict)


class TestsConfig(_Model):
    """Test placement and genuine parameters for test analyses."""

    layout: Literal["mirror", "none"] = Field(
        default="none",
        description="mirror: every test file must mirror a source module.",
    )
    mirror: str = Field(
        default="{path}/test_{module}.py",
        description="Test path relative to the test root; placeholders {root}, {path}, {module}.",
    )
    mirror_exclude: list[str] = Field(
        default_factory=list,
        description='Test path globs exempt from mirroring, e.g. ["tests/e2e/**"].',
    )
    mirror_suffixes: bool = Field(
        default=False, description="Also allow test_<module>_<topic>.py."
    )
    quality_exclude: list[str] = Field(
        default_factory=list,
        description="Project-relative globs excluded from test-quality checks, independently of test mirroring.",
    )
    forbidden_mock_paths: list[str] = Field(
        default_factory=list,
        description="Project-relative test globs forbidding recognized unittest.mock, pytest-mock and MonkeyPatch attribute replacement, including fixtures (TST003). Environment/mapping setup is not counted as MonkeyPatch mocking.",
    )
    assertion_helpers: list[str] = Field(
        default_factory=list,
        description="Qualified callable names accepted as assertion evidence, e.g. tests.assertions.assert_user.",
    )
    timeout_helpers: list[str] = Field(
        default_factory=list,
        description="Qualified context/wrapper helpers known to bound async waits, e.g. tests.timing.deadline. They do not supply assertion evidence or bound synchronous subprocess calls.",
    )

    @field_validator("quality_exclude", "forbidden_mock_paths")
    @classmethod
    def _quality_patterns(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            pattern = value.strip().replace("\\", "/")
            if (
                not pattern
                or pattern.startswith("/")
                or re.match(r"^[A-Za-z]:", pattern)
                or ".." in pattern.split("/")
            ):
                raise ValueError(
                    "test quality globs must be nonempty project-relative paths without '..'"
                )
            result.append(posixpath.normpath(pattern))
        return list(dict.fromkeys(result))

    @field_validator("assertion_helpers", "timeout_helpers")
    @classmethod
    def _assertion_helpers(cls, values: list[str]) -> list[str]:
        return _check_dotted(values)

    @field_validator("mirror")
    @classmethod
    def _mirror_is_a_file_pattern(cls, value: str) -> str:
        unknown = set(re.findall(r"\{[^}]*\}", value)) - {
            "{path}",
            "{module}",
            "{root}",
        }
        if unknown:
            msg = f'mirror "{value}" has unknown placeholders: {", ".join(sorted(unknown))}'
            raise ValueError(msg)
        if value.count("{module}") != 1 or not value.endswith(".py"):
            msg = f'mirror "{value}" needs one "{{module}}" and must end with ".py"'
            raise ValueError(msg)
        if (
            value.startswith("/")
            or value.count("{path}") > 1
            or value.count("{root}") > 1
        ):
            msg = f'mirror "{value}" must be relative, with {{path}} and {{root}} at most once'
            raise ValueError(msg)
        return value


class IgnoreEntry(_Model):
    rule: str = Field(
        description="Rule name, family, code prefix or ALL to ignore within the specified scope."
    )
    modules: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)
    reason: str

    @model_validator(mode="after")
    def _has_scope(self) -> Self:
        if not self.modules and not self.paths:
            msg = "an ignore entry needs `modules` or `paths`"
            raise ValueError(msg)
        if not self.reason.strip():
            msg = "`reason` must not be empty"
            raise ValueError(msg)
        return self


class SuppressionsConfig(_Model):
    """Inline `# smelt: ignore[CODE] -- reason` comments."""

    require_reason: bool = True


class MemberProjectConfig(_Model):
    """Exclusions of one workspace member; its roots come from the workspace root."""

    exclude: list[str | SourceExclusion] = Field(
        default_factory=list,
        description="Member-relative path globs or {path, reason} entries; added to the root exclusions.",
    )


class MemberStyleConfig(_ReasonedStyle):
    """Conventions and package overrides for one workspace member."""

    overrides: dict[str, StyleOverride] = Field(default_factory=dict)


class MemberStructureConfig(_ReasonedStyle, RuleLimits):
    contracts: dict[str, PackageContract] = Field(default_factory=dict)


class MemberConfig(_Model):
    """A workspace member's own smelt.yml, refining the root policy for its files."""

    version: Literal[2] | None = None
    project: MemberProjectConfig = Field(default_factory=MemberProjectConfig)
    style: MemberStyleConfig = Field(default_factory=MemberStyleConfig)
    rules: dict[str, RuleSetting] = Field(
        default_factory=dict,
        description="Rule settings for this member's source and test files. They replace the root settings for the same rules; package contracts still win.",
    )
    ignore: list[IgnoreEntry] = Field(
        default_factory=list,
        description="Rules to skip; paths are relative to the member.",
    )
    architecture: ArchitectureConfig | None = Field(
        default=None,
        description="Features, layers and package APIs for this member. Local architecture belongs to the member and is never inherited.",
    )
    debt: str | None = Field(
        default=None,
        description="Member-relative debt file. It may accept findings only inside this member; stored finding paths remain workspace-relative.",
    )
    _structure: MemberStructureConfig = PrivateAttr(
        default_factory=MemberStructureConfig
    )

    @property
    def structure(self) -> MemberStructureConfig:
        """Combined settings for the analysis and workspace policy compiler."""
        return self._structure

    @property
    def governs_architecture(self) -> bool:
        """Whether this member declares its own internal architecture."""
        return bool(self.architecture and self.architecture.has_layout)

    @model_validator(mode="after")
    def _compile_policy(self) -> Self:
        packages = self.architecture.packages if self.architecture else {}
        style = self.style
        contracts = _combine_packages(packages, style.overrides)
        self._structure = MemberStructureConfig(
            **style.model_dump(exclude={"overrides"}, by_alias=True),
            contracts=contracts,
        )
        self._structure = _with_limits(self._structure, self.rules)
        return self

    _packages: tuple[str, ...] = PrivateAttr(default=())

    @property
    def packages(self) -> tuple[str, ...]:
        """The member's top-level packages, discovered from the workspace roots."""
        return self._packages

    def with_packages(self, packages: Iterable[str]) -> Self:
        located = self.model_copy()
        located._packages = tuple(packages)
        return located

    @field_validator("architecture")
    @classmethod
    def _omit_empty_architecture(
        cls, value: ArchitectureConfig | None
    ) -> ArchitectureConfig | None:
        return value if value is not None and value.model_fields_set else None

    @field_validator("debt")
    @classmethod
    def _debt_is_member_relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip().replace("\\", "/")
        if (
            not path
            or path.startswith("/")
            or re.match(r"^[A-Za-z]:", path)
            or ".." in path.split("/")
            or path == "."
        ):
            raise ValueError("debt must name a member-relative file without '..'")
        return posixpath.normpath(path)

    @field_validator("rules")
    @classmethod
    def _rule_keys(cls, values: dict[str, RuleSetting]) -> dict[str, RuleSetting]:
        return _check_rule_keys(values)


class SmeltConfig(_Model):
    version: Literal[2]
    project: ProjectConfig
    architecture: ArchitectureConfig = Field(default_factory=ArchitectureConfig)
    style: StyleConfig = Field(default_factory=StyleConfig)
    tests: TestsConfig = Field(default_factory=TestsConfig)
    rules: dict[str, RuleSetting] = Field(
        default_factory=dict,
        description="Enable individual checks by name or code. on uses the catalog severity and off disables the check. Families and ALL can only disable or cap active rules.",
    )
    ignore: list[IgnoreEntry] = Field(
        default_factory=list, description="Rules to skip for modules or paths."
    )
    suppressions: SuppressionsConfig = Field(
        default_factory=SuppressionsConfig,
        description="Inline `# smelt: ignore` rules.",
    )
    debt: str | None = Field(
        default=None,
        description="Debt file with known violations, e.g. .smelt/debt.json.",
    )
    members: dict[str, MemberConfig] = Field(
        default_factory=dict,
        description="Workspace member configs by member path, read from each member's own smelt.yml; never written in the root file.",
    )
    _structure: StructureConfig = PrivateAttr(default_factory=StructureConfig)

    @property
    def structure(self) -> StructureConfig:
        """Combined style and package policy for the analysis."""
        return self._structure

    def with_compiled_structure(self, structure: StructureConfig) -> Self:
        scoped = self.model_copy()
        scoped._structure = structure
        return scoped

    @model_validator(mode="after")
    def _compile_policy(self) -> Self:
        style = self.style
        architecture = self.architecture
        self._structure = StructureConfig(
            **style.model_dump(exclude={"overrides"}, by_alias=True),
            contracts=_combine_packages(architecture.packages, style.overrides),
            canonical_exports=architecture.canonical_exports,
        )
        self._structure = _with_limits(self._structure, self.rules)
        return self

    _rule_layers: tuple[dict[str, RuleSetting], ...] = PrivateAttr(default=())
    _workspace_base: Self | None = PrivateAttr(default=None)
    _workspace_packages: dict[str, tuple[str, ...]] = PrivateAttr(default_factory=dict)

    @property
    def workspace_packages(self) -> Mapping[str, tuple[str, ...]]:
        """Top-level packages by workspace directory, with an empty key for root."""
        return self._workspace_packages

    def with_workspace_packages(self, packages: Mapping[str, Iterable[str]]) -> Self:
        scoped = self.model_copy()
        scoped._workspace_packages = {
            path: tuple(names) for path, names in packages.items()
        }
        return scoped

    @property
    def workspace_base(self) -> Self | None:
        """Root policy before member options and contracts were compiled into it."""
        if self._workspace_base is None:
            return None
        return self._workspace_base.with_rule_layers(self.rule_layers)

    def with_workspace_base(self, base: Self) -> Self:
        scoped = self.model_copy()
        scoped._workspace_base = base
        return scoped

    @property
    def rule_layers(self) -> tuple[Mapping[str, RuleSetting], ...]:
        """The rules map of every composed file, base first.

        A later file's selection replaces what an earlier file set for the same
        rules, so a project family overrides an individual entry of its base.
        """
        return self._rule_layers or (self.rules,)

    @property
    def max_patches_per_test(self) -> int | None:
        """The max of the root tests/excess-patches entry."""
        return _rule_limits(self.rules).get("max_patches_per_test")

    @property
    def max_uncovered_facade_callers(self) -> int:
        """The max of the root meta/uncovered-package-facade entry, or its default."""
        return _rule_limits(self.rules).get(
            "max_uncovered_facade_callers",
            POLICY_DEFAULTS.max_uncovered_facade_callers,
        )

    @property
    def strict_coverage(self) -> bool:
        """Whether the root rules enable architecture/uncovered-source.

        The rule is a guard: once enabled it stays an error, and broad
        selectors, member or package settings, suppressions and debt cannot hide it.
        """
        value: RuleSetting | None = None
        for layer in self.rule_layers:
            for key in UNCOVERED_SOURCE:
                if key in layer:
                    value = layer[key]
        severity = value.severity if isinstance(value, RuleOverride) else value
        return severity not in (None, "off")

    def with_strict_coverage(self) -> Self:
        """Enable architecture/uncovered-source for one run, as --strict-coverage does."""
        if self.strict_coverage:
            return self
        return self.with_rule_layers(
            (*self.rule_layers, {UNCOVERED_SOURCE[1]: "error"})
        )

    def with_rule_layers(self, layers: Iterable[Mapping[str, RuleSetting]]) -> Self:
        layered = self.model_copy()
        adapter = TypeAdapter(dict[str, RuleSetting])
        layered._rule_layers = tuple(adapter.validate_python(layer) for layer in layers)
        return layered

    @field_validator("rules")
    @classmethod
    def _rule_codes_are_well_formed(
        cls, values: dict[str, RuleSetting]
    ) -> dict[str, RuleSetting]:
        return _check_rule_keys(values)


def _combine_packages(
    boundaries: Mapping[str, PackageBoundary],
    overrides: Mapping[str, StyleOverride],
) -> dict[str, PackageContract]:
    contracts = {}
    for name in dict.fromkeys((*boundaries, *overrides)):
        if not name.strip():
            raise ValueError("package policy names must not be empty")
        boundary = boundaries.get(name)
        override = overrides.get(name)
        api = boundary.model_dump(by_alias=True) if boundary else {}
        style = override.model_dump(by_alias=True) if override else {}
        if (
            boundary is not None
            and override is not None
            and boundary.match != override.match
        ):
            raise ValueError(
                f'package policy "{name}" must use the same match in architecture.packages and style.overrides'
            )
        if boundary is not None and not (
            boundary.required
            or boundary.public is not None
            or boundary.min_imports_to_bundle is not None
            or boundary.min_importers_to_export is not None
        ):
            raise ValueError(f"architecture.packages.{name} has no package policy")
        if override is not None and not (override.configured() or override.rules):
            raise ValueError(f"style.overrides.{name} has no style policy")
        contract = PackageContract.model_validate(api | style)
        if override:
            contract = _with_limits(contract, override.rules)
        contracts[name] = (
            contract.with_style_location(f"style.overrides.{name}", override=True)
            if override
            else contract
        )
    return contracts


def _with_limits[T: _ReasonedStyle](policy: T, rules: Mapping[str, RuleSetting]) -> T:
    """Copy ``policy`` with the style limits ``rules`` set and the reasons given for them."""
    update: dict[str, object] = {}
    reasons = dict(policy.reasons)
    for option, keys in LIMIT_RULES.items():
        if option not in RuleLimits.model_fields:
            continue
        for key in keys:
            setting = rules.get(key)
            if isinstance(setting, RuleOverride) and setting.max is not None:
                update[option] = setting.max
                if setting.reason is not None:
                    reasons[option] = setting.reason
    if reasons != policy.reasons:
        update["reasons"] = reasons
    return policy.model_copy(update=update)


def _check_rule_keys(values: dict[str, RuleSetting]) -> dict[str, RuleSetting]:
    for key in values:
        if key != ALL_RULES and not (_RULE_CODE.match(key) or _RULE_NAME.match(key)):
            msg = (
                f'"{key}" is not a rule code like "ARC001", '
                'a rule name like "architecture/layer-boundary", '
                'a family like "modeling" or ALL'
            )
            raise ValueError(msg)
    return values
